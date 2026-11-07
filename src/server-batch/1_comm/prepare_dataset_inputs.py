"""Reconstruct source projections into a NEW snapshot, without media/model work."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS
from transcript_dataset import read_dataset, checksum, write
import transcript_common as transcript


def identity(row):
    return (row['t'], float(row.get('s') or 0), float(row.get('d') or 0), row.get('src', ''), row['text'])


def prepare_snapshot(mission, dataset_manifest, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    dataset = read_dataset(dataset_manifest)
    output.mkdir(parents=True)
    # The collector only writes its explicitly redirected precedence audit.
    rows, structured, sources, _ = importlib.import_module('1_comm.1c_prepare_comm').collect_comm(mission, output/'loop-audit')
    by_uid = defaultdict(list)
    for row in rows:
        by_uid[transcript.make_uid('comm', row[0], row[4])].append(dict(zip(('t','s','d','src','text'), row)))
    for name, lines in (
        ('comm.csv', ['|'.join(row) for row in rows]),
        ('PAO.csv', [row['t']+'|'+row['text'] for row in dataset['utterances'] if row['channel']=='pao'])):
        # Preserve the old Windows source projection serialization explicitly.
        (output/name).write_bytes(('\r\n'.join(lines)+'\r\n').encode('utf-8'))
    write(output/'comm.json', [structured[row] for row in rows])
    referenced = {row[3] for row in rows}
    write(output/'comm_sources.json', {key:value for key,value in sources.items() if key in referenced})
    snapshot_mission = SimpleNamespace(transcript_input=lambda name: output/name)
    current = transcript.load_all_utterances(snapshot_mission)
    (output/'current-source-rows.jsonl').write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in current), encoding='utf-8')
    old_comm = [r for r in dataset['utterances'] if r['channel']=='comm']
    new_comm = [r for r in current if r['channel']=='comm']
    old_by_uid = {r['uid']:r for r in old_comm}
    old_identities = Counter(identity(row) for row in old_comm)
    new_identities = Counter(identity(row) for row in new_comm)
    duplicates = []
    for uid, candidates in by_uid.items():
        if len(candidates)>1:
            accepted = old_by_uid.get(uid)
            duplicates.append({'uid':uid, 'rows':candidates,
                'collapsed_rows':len(candidates)-1,
                'distinct_full_identities':len({identity(r) for r in candidates}),
                'distinct_audio_sources':len({r['src'] for r in candidates}),
                'legacy_selected_row':accepted,
                'legacy_selected_matches_first':bool(accepted and identity(accepted)==identity(candidates[0]))})
    write(output/'duplicate-inventory.json', duplicates)
    expected = defaultdict(set)
    for key, value in dataset.items():
        if key.endswith('_report') and isinstance(value, dict):
            for path, digest in value.get('input_hashes', {}).items():
                name = str(path).replace('\\','/').rsplit('/',1)[-1]
                if name in {'comm.csv','PAO.csv'}:
                    expected[name].add(digest)
    hashes = {name:checksum(output/name) for name in ('comm.csv','PAO.csv')}
    report = {'contract':'prepared-transcript-inputs-v1', 'complete':True,
        'dataset_manifest_sha256':checksum(dataset_manifest), 'source_rows':len(rows),
        'legacy_comm_utterances':len(old_comm), 'current_loader_comm_utterances':len(new_comm),
        'legacy_uid_collapsed_rows':sum(r['collapsed_rows'] for r in duplicates),
        'duplicate_groups':len(duplicates),
        'duplicate_groups_with_distinct_full_identities':sum(r['distinct_full_identities']>1 for r in duplicates),
        'duplicate_groups_with_distinct_audio_sources':sum(r['distinct_audio_sources']>1 for r in duplicates),
        'legacy_selected_differs_from_first':sum(not r['legacy_selected_matches_first'] for r in duplicates),
        'missing_legacy_identities':[{'identity':list(k),'count':v} for k,v in (old_identities-new_identities).items()],
        'new_current_identities':[{'identity':list(k),'count':v} for k,v in (new_identities-old_identities).items()],
        'current_loader_identity_equivalent':old_identities == new_identities,
        'hashes':hashes, 'historical_expected_hashes':{key:sorted(value) for key,value in expected.items()},
        'historical_hash_matches':{name:hashes[name] in expected[name] for name in hashes},
        'artifacts':{path.name:checksum(path) for path in output.iterdir() if path.is_file()}}
    write(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mission', required=True, choices=list(MISSIONS))
    parser.add_argument('--dataset-manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args=parser.parse_args()
    print(json.dumps(prepare_snapshot(MISSIONS[args.mission],args.dataset_manifest,args.output),indent=2))


if __name__=='__main__':
    main()
