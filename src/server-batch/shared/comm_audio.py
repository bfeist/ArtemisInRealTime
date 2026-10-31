"""Publish final comm references and retire unused playback files outside web/."""

import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from shared.pds import atomic_json


def audio_relative_path(source):
    """Match the viewer's legacy date-folder rule without using utterance UTC."""
    if not isinstance(source, str) or not source or any(c in source for c in "\\:\x00"):
        raise ValueError(f"Invalid comm audio reference: {source!r}")
    if any(part in {"", ".", ".."} for part in source.split("/")):
        raise ValueError(f"Invalid comm audio reference: {source!r}")
    if "/" not in source:
        date = re.search(r"\d{4}-\d{2}-\d{2}", source)
        if date:
            source = date[0] + "/" + source
    return source


def _inside(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Comm audio path escapes {root}: {path}")
    return path


def _roots(mission):
    web = mission.web_dir / "comm"
    cache = mission.comm_audio_cache
    if not web.resolve().is_relative_to(mission.web_dir.resolve()):
        raise ValueError("web/comm resolves outside the mission web directory")
    if not cache.resolve().is_relative_to((mission.data_dir / "processed").resolve()):
        raise ValueError("Comm audio cache resolves outside the mission processed directory")
    if cache.resolve().is_relative_to(web.resolve()) or web.resolve().is_relative_to(cache.resolve()):
        raise ValueError("Comm audio cache and publication directories must be separate")
    return web, cache


def _files(root):
    files = {}
    if root.exists():
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            _inside(root, relative)
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError(f"Linked paths are not supported in web/comm: {path}")
            if path.is_file():
                files[relative] = path
    return files


def _publish(source, target):
    """Link same-drive immutable derivatives; fall back to an atomic copy."""
    if target.exists() and source.samefile(target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def cached_audio_path(mission, source):
    """Reuse legacy prepared audio when an importer first switches to the cache."""
    web, cache = _roots(mission)
    relative = audio_relative_path(source)
    cached = _inside(cache, relative)
    legacy = _inside(web, relative)
    if not cached.exists() and legacy.is_file() and legacy.stat().st_size:
        _publish(legacy, cached)
    return cached


def prepare_comm_audio(mission, rows):
    """Preflight every final reference before changing any published indexes.

    Existing web audio is adopted into the cache on the first run. Imports and
    transcription write new derivatives there, so future builds need no migration.
    Missing/empty playback files fail the build before any retirement occurs.
    """
    web, cache = _roots(mission)
    published = _files(web)
    counts = Counter()
    sources = defaultdict(set)
    for row in rows:
        relative = audio_relative_path(row[3])
        counts[relative] += 1
        sources[relative].add(row[3])
    if not counts:
        raise RuntimeError("Refusing to publish empty comm or retire all audio; check transcript inputs")

    selected = {}
    missing = []
    for relative in sorted(counts):
        cached = _inside(cache, relative)
        source = cached if cached.is_file() else published.get(relative)
        if source is None or not source.is_file() or source.stat().st_size == 0:
            missing.append(relative)
        else:
            selected[relative] = source
    if missing:
        raise RuntimeError(f"Missing or empty comm audio ({len(missing)}): {', '.join(missing[:10])}. "
                           "Run the corresponding transcription/PCD/loop import before step 1c.")

    manifest = {}
    for relative, source in selected.items():
        cached = _inside(cache, relative)
        _publish(source, cached)
        _publish(cached, _inside(web, relative))
        manifest[relative] = {"bytes": cached.stat().st_size, "utterances": counts[relative],
                              "sources": sorted(sources[relative])}
    return manifest


def reconcile_comm_audio(mission, manifest):
    """Run only after all comm indexes have been saved successfully.

    Preserve every retired file in processed/, including older versions that
    collide with a current cache entry. Originals and transcript JSONs are untouched.
    """
    web, cache = _roots(mission)
    published = _files(web)
    if not manifest or any(relative not in published for relative in manifest):
        raise RuntimeError("Comm audio publication is incomplete; no audio retired")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    # Resolve every source and destination before moving the first file.
    moves = []
    for relative, source in sorted(published.items()):
        if relative in manifest:
            continue
        target = _inside(cache, relative)
        if target.exists() and not source.samefile(target):
            target = _inside(cache, "_retired/" + stamp + "/" + relative)
        moves.append((relative, source, target, source.stat().st_size))

    retired = []
    for relative, source, target, size in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and source.samefile(target):
            source.unlink()  # The cache retains the same bytes through its hard link.
        else:
            os.replace(source, target)
        retired.append({"path": relative, "bytes": size,
                        "cache": target.relative_to(cache).as_posix()})
    for directory in sorted((p for p in web.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        _inside(web, directory.relative_to(web).as_posix())
        if not any(directory.iterdir()):
            directory.rmdir()

    report = {"date": datetime.now(timezone.utc).isoformat(), "files": manifest,
              "published_files": len(manifest), "published_bytes": sum(r["bytes"] for r in manifest.values()),
              "retired_files": len(retired), "retired_bytes": sum(r["bytes"] for r in retired),
              "retired": retired}
    processed = mission.data_dir / "processed"
    atomic_json(processed / "comm_audio_manifest.json", report)
    if retired:
        atomic_json(processed / "comm_audio_reconciliations" / (stamp + ".json"), report)
    print(f"  Comm audio: {len(manifest)} published; {len(retired)} retired to processed/comm_audio "
          f"({report['retired_bytes'] / (1 << 20):.1f} MiB)")
    return report
