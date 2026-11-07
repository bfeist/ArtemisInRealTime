"""Immutable, allowlisted web releases. Staging never changes the active pointer."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from shared.comm_audio import audio_relative_path
from shared.pds import atomic_json
from shared.pao_sync import epoch, finite, compact_sync_projection


def _validate_public_playback(root):
    parts_path = root / 'video-parts.json'
    parts = {}
    if parts_path.exists():
        catalog = json.loads(parts_path.read_text(encoding='utf-8'))
        if catalog.get('schemaVersion') != 1 or not isinstance(catalog.get('parts'), list) or not catalog['parts'] or catalog.get('timingStatus') != 'candidate':
            raise ValueError('Invalid public part catalog')
        epoch(catalog['streamStartUtc'])
        previous_end = 0
        for index, part in enumerate(catalog['parts'], 1):
            identifier = part.get('videoId', '')
            if part.get('partNum') != index or not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier) or identifier in parts:
                raise ValueError('Invalid uploaded part identity/order')
            if finite(part.get('durationS')) <= 0 or finite(part.get('startOffsetS')) < 0 or finite(part.get('originalStartSeconds')) < 0:
                raise ValueError('Invalid public part timing')
            if finite(part['startOffsetS']) < previous_end - 0.001:
                raise ValueError('Overlapping candidate playback parts')
            previous_end = finite(part['startOffsetS']) + finite(part['durationS'])
            if part.get('timingStatus') != 'candidate':
                raise ValueError('Unknown public part timing status')
            parts[identifier] = part
    sync_path = root / 'pao-sync.json'
    if sync_path.exists():
        sync = json.loads(sync_path.read_text(encoding='utf-8'))
        if sync.get('schemaVersion') != 1 or not isinstance(sync.get('mappings'), list):
            raise ValueError('Invalid public synchronization schema')
        seen = set()
        intervals = []
        for piece in sync['mappings']:
            required = {'id','feed','startUtc','endUtc','videoId','videoStartSeconds','rate','recordingId','recordingStartSeconds'}
            if set(piece) != required or not all(isinstance(piece[key],str) and piece[key] for key in ('id','feed','recordingId')) or piece['id'] in seen:
                raise ValueError('Invalid public synchronization mapping')
            seen.add(piece['id'])
            part = parts.get(piece['videoId'])
            start, end = epoch(piece['startUtc']), epoch(piece['endUtc'])
            offset, rate = finite(piece['videoStartSeconds']), finite(piece['rate'])
            if not part or end <= start or offset < 0 or rate <= 0 or finite(piece['recordingStartSeconds']) < 0 or offset+(end-start)*rate > part['durationS']+0.05:
                raise ValueError('Synchronization mapping outside uploaded part bounds')
            intervals.append((piece['feed'],start,end))
        intervals.sort()
        if any(a[0] == b[0] and a[2] > b[1] for a,b in zip(intervals,intervals[1:])):
            raise ValueError('Overlapping public synchronization mappings')


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _safe(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError(f'Unsafe release reference: {relative!r}')
    path = root / relative
    if not relative or any(p in {'', '.', '..'} for p in relative.split('/')) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Unsafe release reference: {relative!r}')
    return path


def _read_pointer(web):
    path = web / 'active-release.json'
    if not path.exists():
        return None
    pointer = json.loads(path.read_text(encoding='utf-8'))
    identifier = pointer.get('release_id')
    if pointer.get('schema_version') != 1 or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
        raise ValueError('Malformed active release pointer')
    if pointer.get('base_url') != f'releases/{identifier}/' or pointer.get('manifest') != 'release-manifest.json':
        raise ValueError('Malformed active release pointer paths')
    return pointer


def _stat(path):
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


def _audio_references(transcripts):
    references = {'comm/' + audio_relative_path(row['src']) for row in transcripts.get('comm', [])}
    if any(Path(reference).suffix.lower() not in {'.aac','.m4a'} for reference in references):
        raise ValueError('Only prepared AAC/M4A playback derivatives may be released')
    for row in transcripts.get('pao', []):
        playback = row.get('playback')
        if playback is None:
            continue
        if not isinstance(playback, dict) or not all(isinstance(playback.get(key), str) and playback[key] for key in ('source', 'recordingId', 'feed')):
            raise ValueError('Invalid PAO playback metadata')
        audio = playback.get('audio')
        if audio is not None:
            source = audio.get('src') if isinstance(audio, dict) else None
            if not isinstance(source, str) or not source.startswith('comm/') or Path(source).suffix.lower() not in {'.aac', '.m4a'} or finite(audio.get('s')) < 0:
                raise ValueError('Invalid PAO audio playback reference')
            if 'comm/' + audio_relative_path(source[5:]) != source:
                raise ValueError('PAO audio reference must use explicit canonical path')
            if finite(audio.get('d')) <= 0:
                raise ValueError('Invalid PAO audio duration')
            references.add(source)
        video = playback.get('video')
        if video is not None and (not isinstance(video,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video.get('id','')) or not isinstance(video.get('mappingId'),str) or not video['mappingId'] or finite(video.get('s')) < 0):
            raise ValueError('Invalid PAO video playback metadata')
        if video is not None and finite(video.get('d')) <= 0:
            raise ValueError('Invalid PAO video duration')
        if audio is None and video is None:
            raise ValueError('PAO playback lacks usable media')
    return references


def verify_release(root, *, _shared_evidence=None):
    root = Path(root)
    manifest = json.loads((root / 'release-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('release_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',manifest['release_id']) or not isinstance(manifest.get('assets'),dict) or not isinstance(manifest.get('shared_assets', {}),dict):
        raise ValueError('Invalid release manifest schema')
    _validate_public_playback(root)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != set(manifest['assets']) | {'release-manifest.json'}:
        raise ValueError('Release allowlist does not match staged files')
    for name, entry in manifest['assets'].items():
        path = _safe(root, name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['bytes'] or _digest(path) != entry['sha256']:
            raise ValueError(f'Release asset failed verification: {name}')
        if name.endswith('.json.gz'):
            original = _safe(root, name[:-3])
            if name[:-3] not in manifest['assets'] or gzip.decompress(path.read_bytes()) != original.read_bytes():
                raise ValueError(f'Invalid gzip representation: {name}')
    for name, entry in manifest.get('shared_assets', {}).items():
        path = _safe(root.parent.parent, name)
        evidence = (_shared_evidence or {}).get(name)
        before = _stat(path)
        digest = evidence[1] if evidence and evidence[0] == before else _digest(path)
        if before != _stat(path):
            raise ValueError(f'Shared playback dependency changed during verification: {name}')
        if not path.is_file() or path.stat().st_size != entry['bytes'] or digest != entry['sha256']:
            raise ValueError(f'Shared playback dependency changed: {name}')
        if _shared_evidence is not None:
            _shared_evidence[name] = (before, digest)
    transcripts = json.loads((root / 'transcripts.json').read_text(encoding='utf-8'))
    audio_references = _audio_references(transcripts)
    sync_file = root / 'pao-sync.json'
    sync = json.loads(sync_file.read_text(encoding='utf-8')) if sync_file.exists() else {'mappings':[]}
    mappings = {piece['id']:piece for piece in sync['mappings']}
    for row in transcripts.get('pao', []):
        video = (row.get('playback') or {}).get('video')
        if video:
            mapping = mappings.get(video['mappingId'])
            if not mapping or mapping['videoId'] != video['id']:
                raise ValueError('PAO video playback lacks matching verified synchronization')
            playback = row['playback']
            instant = epoch(row['t'])
            if mapping['feed'] != playback['feed'] or mapping['recordingId'] != playback['recordingId'] or not epoch(mapping['startUtc']) <= instant < epoch(mapping['endUtc']):
                raise ValueError('PAO playback attribution does not match synchronization')
            expected_offset = mapping['videoStartSeconds'] + (instant - epoch(mapping['startUtc'])) * mapping['rate']
            if abs(video['s'] - expected_offset) > 0.05:
                raise ValueError('PAO video offset does not match UTC/rate synchronization')
            mapping_end = mapping['videoStartSeconds'] + (epoch(mapping['endUtc']) - epoch(mapping['startUtc'])) * mapping['rate']
            if video['s'] < mapping['videoStartSeconds'] - 0.05 or video['s'] + video.get('d',0) > mapping_end + 0.05:
                raise ValueError('PAO video playback interval exceeds verified synchronization')
    for name in audio_references:
        if name not in manifest['assets']:
            raise ValueError(f'Missing playback reference: {name}')
    for name, receipt in manifest.get('pao_audio_receipts', {}).items():
        if name not in manifest['assets'] or any(manifest['assets'][name].get(key) != receipt.get(key) for key in ('bytes','sha256')):
            raise ValueError(f'Reviewed PAO audio derivative changed: {name}')
    timeline_path = root / 'timeline.json'
    if timeline_path.exists():
        timeline = json.loads(timeline_path.read_text(encoding='utf-8'))
        for crew in timeline.get('crew', {}).values():
            reference = crew.get('timeline', {})
            if reference.get('path') not in manifest['assets']:
                raise ValueError('Missing crew timeline reference')
            path = _safe(root, reference['path'])
            if reference.get('bytes') is not None and path.stat().st_size != reference['bytes']:
                raise ValueError('Crew timeline byte count changed')
    for name in ('videoIA.json', 'videoIO.json', 'videoPDS.json', 'videoYt.json'):
        if (root / name).is_file():
            for video in json.loads((root / name).read_text(encoding='utf-8')):
                url = video.get('webUrl')
                if url and url.lstrip('/') not in manifest.get('shared_assets', {}):
                    raise ValueError(f'Missing public video reference: {url}')
    return manifest


def stage_release(mission, transcripts, *, release_id=None, exports=None, sync_path=None, provenance=None, pao_audio_receipts=None):
    """Copy selected derivatives and client indexes; never copy raw/processed trees."""
    release_id = release_id or uuid4().hex
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', release_id):
        raise ValueError('Invalid release ID')
    releases = mission.web_dir / 'releases'
    current_pointer = _read_pointer(mission.web_dir)
    releases.mkdir(parents=True, exist_ok=True)
    selected_audio = {}
    for reference in sorted(_audio_references(transcripts)):
        relative = reference[5:]
        source = _safe(mission.comm_audio_cache, relative)
        if not source.is_file():
            source = _safe(mission.web_dir / 'comm', relative)
        if not source.is_file() or not source.stat().st_size:
            raise ValueError(f'Missing/empty release dependency: {source}')
        selected_audio[relative] = source
    if pao_audio_receipts is not None:
        for row in transcripts.get('pao', []):
            audio = (row.get('playback') or {}).get('audio')
            if audio and audio['src'] not in pao_audio_receipts:
                raise ValueError('Selected PAO audio lacks reviewed derivative receipt')
    required_bytes = sum(path.stat().st_size for path in selected_audio.values())
    if shutil.disk_usage(releases).free < required_bytes + (64 << 20):
        raise OSError(f'Insufficient space for staged audio ({required_bytes} bytes plus reserve)')
    destination = releases / release_id
    if destination.exists():
        raise FileExistsError(destination)
    staging = releases / ('.staging-' + uuid4().hex)
    staging.mkdir()
    try:
        shared_assets = {}
        shared_evidence = {}
        def compact(name, value):
            path = _safe(staging, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

        def copy(name, source):
            target = _safe(staging, name)
            if not source.is_file() or source.stat().st_size == 0:
                raise ValueError(f'Missing/empty release dependency: {source}')
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copies deliberately isolate immutable releases from mutable cache files.
            shutil.copyfile(source, target)

        if not transcripts.get('comm') and not transcripts.get('pao'):
            raise ValueError('Refusing empty transcript release')
        compact('transcripts.json', transcripts)
        for relative, source_audio in selected_audio.items():
            copy('comm/' + relative, source_audio)
        for name in ('timeline.json', 'videoIA.json', 'videoYt.json', 'videoPDS.json', 'videoIO.json'):
            source = mission.web_dir / name
            if source.is_file():
                payload = json.loads(source.read_text(encoding='utf-8'))
                if name == 'timeline.json':
                    for crew in payload.get('crew', {}).values():
                        reference = crew.get('timeline', {}).get('path')
                        if reference:
                            if not reference.startswith('timeline/crew/') or not reference.endswith('.json'):
                                raise ValueError('Unexpected crew timeline path')
                            copy(reference, _safe(mission.web_dir, reference))
                else:
                    for video in payload:
                        url = video.get('webUrl')
                        if url:
                            relative = url.lstrip('/')
                            if not relative.startswith('video/') or Path(relative).suffix.lower() not in {'.mp4', '.webm'}:
                                raise ValueError(f'Unexpected prepared video path: {url}')
                            source_video = _safe(mission.web_dir, relative)
                            if not source_video.is_file() or not source_video.stat().st_size:
                                raise ValueError(f'Missing prepared video: {relative}')
                            if relative not in shared_assets:
                                before = _stat(source_video)
                                digest = _digest(source_video)
                                if before != _stat(source_video):
                                    raise ValueError(f'Video changed during hashing: {relative}')
                                shared_assets[relative] = {'bytes': before[0], 'sha256': digest}
                                shared_evidence[relative] = (before, digest)
                compact(name, payload)
        if sync_path:
            private_sync = json.loads(Path(sync_path).read_text(encoding='utf-8'))
            if any(piece.get('verification') != 'verified' for piece in private_sync.get('mappings', [])):
                raise ValueError('--sync-path requires private verified mappings with audio evidence')
            private_parts = mission.data_dir / 'processed' / 'pao_sync' / 'video-parts-manifest.json'
            compact('pao-sync.json', compact_sync_projection(private_sync, json.loads(private_parts.read_text(encoding='utf-8'))))
        elif (mission.data_dir / 'processed' / 'pao_sync' / 'pao-sync.json').is_file():
            public_sync = json.loads((mission.data_dir / 'processed' / 'pao_sync' / 'pao-sync.json').read_text(encoding='utf-8'))
            if public_sync.get('mappings'):
                raise ValueError('Automatic public sync must be empty; supply private verified --sync-path')
            compact('pao-sync.json', public_sync)
        elif (mission.data_dir / 'processed' / 'pao_sync' / 'coverage-plan.json').is_file():
            coverage = json.loads((mission.data_dir / 'processed' / 'pao_sync' / 'coverage-plan.json').read_text(encoding='utf-8'))
            if coverage.get('sync') is not None:
                if coverage['sync'].get('mappings'):
                    raise ValueError('Automatic coverage sync must be empty; supply private verified --sync-path')
                compact('pao-sync.json', coverage['sync'])
        parts = mission.data_dir / 'processed' / 'pao_sync' / 'video-parts.json'
        if parts.is_file():
            compact('video-parts.json', json.loads(parts.read_text(encoding='utf-8')))
        if exports:
            exports(staging)
        # Deterministic representations; the HTTP host must negotiate encoding.
        for path in sorted(staging.rglob('*.json')):
            path.with_name(path.name + '.gz').write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        assets = {}
        for path in sorted(staging.rglob('*')):
            if path.is_file():
                name = path.relative_to(staging).as_posix()
                if name not in {'comm_enriched.csv', 'PAO_enriched.csv'} and path.suffix not in {'.json', '.aac', '.m4a', '.mp4', '.webm'} and not name.endswith('.json.gz'):
                    raise ValueError(f'Asset type not allowlisted: {name}')
                assets[name] = {'bytes': path.stat().st_size, 'sha256': _digest(path)}
                if path.suffix == '.json':
                    assets[name]['gzip_bytes'] = path.with_name(path.name + '.gz').stat().st_size
                    assets[name]['gzip_path'] = name + '.gz'
        previous = current_pointer['release_id'] if current_pointer else None
        manifest = {'schema_version': 1, 'release_id': release_id, 'previous_release_id': previous,
                    'assets': assets, 'shared_assets': shared_assets,
                    'shared_asset_policy': 'Pinned existing public video; retain while any release references it',
                    'provenance': provenance or {},
                    'pao_audio_receipts': pao_audio_receipts or {},
                    'object_count': len(assets), 'logical_bytes': sum(v['bytes'] for v in assets.values())}
        baseline = {}
        for name in assets:
            legacy = _safe(mission.web_dir, name)
            if legacy.is_file():
                baseline[name] = {'bytes': legacy.stat().st_size}
        manifest['comparison'] = {'legacy_matching_objects': len(baseline),
                                  'legacy_matching_bytes': sum(v['bytes'] for v in baseline.values()),
                                  'release_json_gzip_bytes': sum(v.get('gzip_bytes', 0) for v in assets.values()),
                                  'reference_complete': True}
        atomic_json(staging / 'release-manifest.json', manifest)
        verify_release(staging, _shared_evidence=shared_evidence)
        os.replace(staging, destination)
        return destination, manifest
    finally:
        if staging.exists():
            if not staging.resolve().is_relative_to(releases.resolve()) or staging.resolve() == releases.resolve():
                raise ValueError('Refusing cleanup outside release staging directory')
            shutil.rmtree(staging)


def activate_release(mission, release_id, *, legacy_baseline=None):
    from shared.ia_video_downloads import plan_lock
    with plan_lock(mission.data_dir / 'processed' / 'web-release-activation.json'):
        return _activate_release(mission, release_id, legacy_baseline=legacy_baseline)


def _activate_release(mission, release_id, *, legacy_baseline=None):
    root = _safe(mission.web_dir / 'releases', release_id)
    shared_evidence = {}
    manifest = verify_release(root, _shared_evidence=shared_evidence)
    if manifest['release_id'] != release_id:
        raise ValueError('Release directory identity mismatch')
    pointer_path = mission.web_dir / 'active-release.json'
    current_pointer = _read_pointer(mission.web_dir)
    previous = current_pointer['release_id'] if current_pointer else legacy_baseline
    if not current_pointer and (mission.web_dir / 'transcripts.json').exists() and not legacy_baseline:
        raise ValueError('First activation requires an explicitly preserved --legacy-baseline release')
    if not current_pointer and legacy_baseline:
        baseline = _safe(mission.web_dir / 'releases', legacy_baseline)
        for name in ('transcripts.json', 'timeline.json', 'videoIA.json', 'videoIO.json', 'videoPDS.json', 'videoYt.json'):
            legacy = mission.web_dir / name
            if legacy.is_file():
                saved = _safe(baseline, name)
                if not saved.is_file() or json.loads(legacy.read_text(encoding='utf-8')) != json.loads(saved.read_text(encoding='utf-8')):
                    raise ValueError(f'Baseline does not preserve legacy {name}')
    if previous:
        verify_release(_safe(mission.web_dir / 'releases', previous), _shared_evidence=shared_evidence)
    pointer = {'schema_version': 1, 'release_id': manifest['release_id'],
               'base_url': f'releases/{release_id}/', 'manifest': 'release-manifest.json',
               'previous_release_id': previous}
    atomic_json(pointer_path, pointer)
    return pointer
