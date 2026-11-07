"""Release interruption, closure, and rollback tests use only disposable fixtures."""
import json
import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from shared.web_release import activate_release, stage_release, verify_release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.mission = SimpleNamespace(web_dir=root/'web', data_dir=root, slug='test',
                                       comm_audio_cache=root/'processed'/'comm_audio')
        self.mission.web_dir.mkdir()
        self.mission.comm_audio_cache.mkdir(parents=True)
        (self.mission.comm_audio_cache/'call.aac').write_bytes(b'old playable audio')
        self.transcripts = {'comm': [{'t':'2026-04-01T00:00:00Z', 'src':'call.aac', 's':1, 'd':2, 'text':'hello'}], 'pao':[]}

    def test_stage_is_compact_and_does_not_activate_or_include_private_inputs(self):
        (self.mission.web_dir/'raw.wav').write_bytes(b'private')
        root, manifest = stage_release(self.mission, self.transcripts, release_id='first')
        self.assertFalse((self.mission.web_dir/'active-release.json').exists())
        self.assertEqual(set(manifest['assets']), {'transcripts.json', 'transcripts.json.gz', 'comm/call.aac'})
        self.assertNotIn(b'\n', (root/'transcripts.json').read_bytes())
        self.assertEqual(verify_release(root), manifest)
        compressed = root/'transcripts.json.gz'
        self.assertEqual(gzip.decompress(compressed.read_bytes()), (root/'transcripts.json').read_bytes())
        second, _ = stage_release(self.mission, self.transcripts, release_id='second')
        self.assertEqual(compressed.read_bytes(), (second/'transcripts.json.gz').read_bytes())

    def test_interruption_and_missing_audio_leave_active_release_playable(self):
        first, _ = stage_release(self.mission, self.transcripts, release_id='first')
        activate_release(self.mission, 'first')
        before = (self.mission.web_dir/'active-release.json').read_bytes()
        with patch('shared.web_release.shutil.copyfile', side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                stage_release(self.mission, self.transcripts, release_id='second')
        self.assertEqual((self.mission.web_dir/'active-release.json').read_bytes(), before)
        self.assertEqual((first/'comm/call.aac').read_bytes(), b'old playable audio')
        self.assertFalse(list((self.mission.web_dir/'releases').glob('.staging-*')))
        missing = {'comm':[{'src':'missing.aac'}], 'pao':[]}
        with self.assertRaises(ValueError):
            stage_release(self.mission, missing)
        verify_release(first)

    def test_cache_changes_and_rollback_do_not_change_old_release(self):
        first, _ = stage_release(self.mission, self.transcripts, release_id='first')
        activate_release(self.mission, 'first')
        (self.mission.comm_audio_cache/'call.aac').write_bytes(b'new audio')
        second, _ = stage_release(self.mission, self.transcripts, release_id='second')
        pointer = activate_release(self.mission, 'second')
        self.assertEqual(pointer['previous_release_id'], 'first')
        self.assertEqual((first/'comm/call.aac').read_bytes(), b'old playable audio')
        self.assertEqual((second/'comm/call.aac').read_bytes(), b'new audio')
        activate_release(self.mission, 'first')
        self.assertEqual(json.loads((self.mission.web_dir/'active-release.json').read_text())['release_id'], 'first')

    def test_tampering_cannot_switch_pointer(self):
        root, _ = stage_release(self.mission, self.transcripts, release_id='first')
        (root/'comm/call.aac').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            activate_release(self.mission, 'first')
        self.assertFalse((self.mission.web_dir/'active-release.json').exists())

    def test_legacy_audio_fallback_does_not_adopt_or_mutate_cache(self):
        (self.mission.comm_audio_cache/'call.aac').unlink()
        legacy = self.mission.web_dir/'comm/call.aac'
        legacy.parent.mkdir()
        legacy.write_bytes(b'legacy audio')
        root, _ = stage_release(self.mission, self.transcripts, release_id='legacy')
        self.assertFalse((self.mission.comm_audio_cache/'call.aac').exists())
        self.assertEqual((root/'comm/call.aac').read_bytes(), b'legacy audio')

    def test_first_activation_requires_equivalent_preserved_baseline(self):
        (self.mission.web_dir/'transcripts.json').write_text(json.dumps(self.transcripts))
        stage_release(self.mission, self.transcripts, release_id='baseline')
        with self.assertRaisesRegex(ValueError, 'legacy-baseline'):
            activate_release(self.mission, 'baseline')
        pointer = activate_release(self.mission, 'baseline', legacy_baseline='baseline')
        self.assertEqual(pointer['previous_release_id'], 'baseline')

    def test_malformed_pointer_fails_before_staging(self):
        (self.mission.web_dir/'active-release.json').write_text('{"release_id":"bad"}')
        with self.assertRaisesRegex(ValueError, 'Malformed'):
            stage_release(self.mission, self.transcripts)
        self.assertFalse((self.mission.web_dir/'releases').exists())

    def test_public_sync_cannot_bypass_private_verification(self):
        sync = self.mission.data_dir/'unsafe-sync.json'
        sync.write_text(json.dumps({'schemaVersion':1,'mappings':[{'videoId':'abcdefghijk'}]}))
        with self.assertRaisesRegex(ValueError, 'private verified'):
            stage_release(self.mission, self.transcripts, sync_path=sync)

    def test_invalid_public_part_catalog_prevents_publication(self):
        parts = self.mission.data_dir/'processed/pao_sync/video-parts.json'
        parts.parent.mkdir(parents=True)
        parts.write_text(json.dumps({'schemaVersion':1,'parts':[{'partNum':1,'videoId':'unknown'}]}))
        with self.assertRaises(ValueError):
            stage_release(self.mission, self.transcripts, release_id='invalid')
        self.assertFalse((self.mission.web_dir/'releases/invalid').exists())

    def test_selected_pao_audio_is_copied_without_rewriting_playback_metadata(self):
        audio = self.mission.comm_audio_cache/'pao/recording.m4a'
        audio.parent.mkdir()
        audio.write_bytes(b'prepared pao')
        payload = {'comm':[], 'pao':[{'text':'Broadcast','playback':{
            'source':'ia','recordingId':'recording','feed':'pao-primary',
            'audio':{'src':'comm/pao/recording.m4a','s':12.5,'d':3}}}]}
        root, manifest = stage_release(self.mission,payload)
        self.assertIn('comm/pao/recording.m4a',manifest['assets'])
        self.assertEqual(json.loads((root/'transcripts.json').read_text()),payload)
        payload['pao'][0]['playback']['audio']['src']='comm/../raw.wav'
        with self.assertRaises(ValueError):
            stage_release(self.mission,payload)

    def test_reviewed_pao_audio_receipt_is_enforced(self):
        payload = {'comm':[], 'pao':[{'text':'Broadcast','playback':{
            'source':'ia','recordingId':'recording','feed':'pao-primary',
            'audio':{'src':'comm/call.aac','s':0,'d':1}}}]}
        receipt = {'comm/call.aac':{'bytes':18,'sha256':'wrong'}}
        with self.assertRaisesRegex(ValueError, 'derivative changed'):
            stage_release(self.mission,payload,pao_audio_receipts=receipt)
        data = (self.mission.comm_audio_cache/'call.aac').read_bytes()
        receipt['comm/call.aac'] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        stage_release(self.mission,payload,pao_audio_receipts=receipt)
        del payload['pao'][0]['playback']['audio']['d']
        with self.assertRaises((ValueError,TypeError)):
            stage_release(self.mission,payload)

    def test_pao_video_attribution_and_utc_rate_formula_are_enforced(self):
        parts = self.mission.data_dir/'processed/pao_sync'
        parts.mkdir(parents=True)
        (parts/'video-parts-manifest.json').write_text(json.dumps({'parts':[{
            'part':1,'filename':'part1.mp4','originalStartSeconds':0,'durationSeconds':100,
            'uploadedVideoId':'abcdefghijk','uploadProvenance':{'kind':'fixture'}}]}))
        (parts/'video-parts.json').write_text(json.dumps({'schemaVersion':1,'timingStatus':'candidate',
            'streamStartUtc':'2026-04-01T00:00:00Z','parts':[{'partNum':1,'videoId':'abcdefghijk',
            'durationS':100,'startOffsetS':0,'originalStartSeconds':0,'timingStatus':'candidate'}]}))
        sync = self.mission.data_dir/'sync-private.json'
        sync.write_text(json.dumps({'mappings':[{'id':'map1','feed':'pao-primary','recordingId':'recording',
            'startUtc':'2026-04-01T00:00:00Z','endUtc':'2026-04-01T00:00:20Z',
            'videoStartSeconds':2,'recordingStartSeconds':0,'rate':2,'partFilename':'part1.mp4',
            'verification':'verified','evidence':[{'kind':'manual-audio-review','reference':'fixture'}]}]}))
        payload = {'comm':[],'pao':[{'t':'2026-04-01T00:00:05Z','text':'Broadcast','playback':{
            'source':'youtube','recordingId':'recording','feed':'pao-primary',
            'video':{'id':'abcdefghijk','s':12,'d':2,'mappingId':'map1'}}}]}
        stage_release(self.mission,payload,sync_path=sync)
        for field, value in [('feed','other'),('recordingId','other')]:
            changed=json.loads(json.dumps(payload))
            changed['pao'][0]['playback'][field]=value
            with self.assertRaisesRegex(ValueError,'attribution'):
                stage_release(self.mission,changed,sync_path=sync)
        changed=json.loads(json.dumps(payload))
        changed['pao'][0]['playback']['video']['s']=13
        with self.assertRaisesRegex(ValueError,'UTC/rate'):
            stage_release(self.mission,changed,sync_path=sync)
        changed=json.loads(json.dumps(payload))
        changed['pao'][0]['t']='2026-04-01T00:00:30Z'
        with self.assertRaisesRegex(ValueError,'attribution'):
            stage_release(self.mission,changed,sync_path=sync)

    def test_crew_reference_and_video_dependency_are_closed(self):
        crew = self.mission.web_dir/'timeline/crew/a.json'
        crew.parent.mkdir(parents=True)
        crew.write_text('{"items": []}')
        (self.mission.web_dir/'timeline.json').write_text(json.dumps({'crew':{'koch':{'timeline':{'path':'timeline/crew/a.json','bytes':crew.stat().st_size}}}}))
        video = self.mission.web_dir/'video/ia/test.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'public video')
        (self.mission.web_dir/'videoIA.json').write_text(json.dumps([{'webUrl':'/video/ia/test.mp4'}]))
        root, manifest = stage_release(self.mission, self.transcripts, release_id='closed')
        self.assertIn('video/ia/test.mp4', manifest['shared_assets'])
        self.assertIn('timeline/crew/a.json', manifest['assets'])
        verify_release(root)


if __name__ == '__main__':
    unittest.main()
