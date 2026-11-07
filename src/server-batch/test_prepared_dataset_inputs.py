import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

prepare = importlib.import_module('1_comm.prepare_dataset_inputs')


class SnapshotTests(unittest.TestCase):
    def test_snapshot_reports_distinct_recording_collapse_without_touching_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comm = root/'originals/comm'
            comm.mkdir(parents=True)
            for name in ('flight_OE_Comp_1', 'flight_OE_Comp_2'):
                (comm/(name+'.json')).write_text(json.dumps({'utcTime':'2026-04-01T00:00:00Z',
                    'tz_corrected':True,'web_src':name+'.aac',
                    'segments':[{'start':1,'end':2,'text':'Same call'}]}))
            before = {path:path.read_bytes() for path in comm.iterdir()}
            manifest = root/'manifest.json'
            manifest.write_text('{}')
            uid = prepare.transcript.make_uid('comm','2026-04-01T00:00:01.000Z','Same call')
            legacy = {'uid':uid,'channel':'comm','t':'2026-04-01T00:00:01.000Z',
                      's':'1','d':'1','src':'flight_OE_Comp_1.aac','text':'Same call'}
            mission=SimpleNamespace(processed_transcripts=root/'originals',comm_tz_offset_hours=0)
            with patch.object(prepare,'read_dataset',return_value={'utterances':[legacy]}):
                report = prepare.prepare_snapshot(mission,manifest,root/'snapshot')
            self.assertEqual(report['source_rows'],2)
            self.assertEqual(report['legacy_uid_collapsed_rows'],1)
            self.assertEqual(report['duplicate_groups_with_distinct_audio_sources'],1)
            self.assertEqual(before,{path:path.read_bytes() for path in comm.iterdir()})
            self.assertFalse((root/'web').exists())
            self.assertIn(b'\r\n',(root/'snapshot/comm.csv').read_bytes())
            current = [json.loads(line) for line in (root/'snapshot/current-source-rows.jsonl').read_text().splitlines()]
            self.assertEqual(len(current), report['current_loader_comm_utterances'])
            self.assertEqual({row['src'] for row in current}, {'flight_OE_Comp_1.aac', 'flight_OE_Comp_2.aac'})
            with self.assertRaises(FileExistsError):
                prepare.prepare_snapshot(mission,manifest,root/'snapshot')


if __name__=='__main__':
    unittest.main()
