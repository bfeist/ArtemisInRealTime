"""Routine commands cannot enter model, download, or upload stages."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import run_all
import refresh_assets


class RunnerTests(unittest.TestCase):
    def mission(self):
        return SimpleNamespace(name='Fixture', ensure_dirs=lambda: None)

    def test_default_is_model_free(self):
        with patch.object(sys, 'argv', ['run_all', '--mission', 'artemis-ii']), \
             patch.dict(run_all.MISSIONS, {'artemis-ii': self.mission()}), \
             patch.object(run_all, 'run_step', return_value=True) as run:
            run_all.main()
        self.assertEqual([call.args[0] for call in run.call_args_list], ['1c', '1i'])

    def test_model_group_rejected_before_execution_without_bounded_plan(self):
        with patch.object(sys, 'argv', ['run_all', '--mission', 'artemis-ii', '--step', 'comm-history']), \
             patch.dict(run_all.MISSIONS, {'artemis-ii': self.mission()}), \
             patch.object(run_all, 'run_step') as run, self.assertRaises(SystemExit):
            run_all.main()
        run.assert_not_called()

    def test_explicit_asr_requires_input_dir(self):
        with patch.object(sys, 'argv', ['run_all', '--mission', 'artemis-ii', '--step', '2j']), \
             patch.dict(run_all.MISSIONS, {'artemis-ii': self.mission()}), \
             patch.object(run_all, 'run_step') as run, self.assertRaises(SystemExit):
            run_all.main()
        run.assert_not_called()

    def test_explicit_asr_forwards_input_dir(self):
        module = SimpleNamespace(main=lambda: None)
        with patch.object(run_all.importlib, 'import_module', return_value=module), \
             patch.object(run_all.importlib, 'reload', return_value=module), \
             patch.object(sys, 'argv', []):
            self.assertTrue(run_all.run_step('2j', 'fixture', 'fixture', 'artemis-ii', input_dir='cached/chunks'))
            self.assertEqual(sys.argv[-2:], ['--input-dir', 'cached/chunks'])

    def test_build_import_order_and_no_model_or_download_jobs(self):
        modules = [job[1] for job in refresh_assets.STAGES['build']]
        self.assertLess(modules.index('7_pds.7f_integrate'), modules.index('1_comm.1b3_pcd_audio'))
        self.assertLess(modules.index('1_comm.1b4_loop_audio'), modules.index('1_comm.1c_prepare_comm'))
        for name in modules:
            self.assertNotIn(name, {'1_comm.1b_transcribe', '1_comm.1d_enrich_transcripts',
                                   '2_video.2j_transcribe_yt', '2_video.2k_filter_yt_transcript',
                                   '2_video.2e_yt_download', '2_video.2i_yt_upload'})

    def test_publication_is_an_explicit_separate_step(self):
        self.assertEqual(refresh_assets.STAGES['publish'], [('asset-publish', 'publish_assets', [])])
        self.assertNotIn('publish_assets', [job[1] for job in refresh_assets.STAGES['build']])
        step = next(step for step in run_all.STEPS if step[0] == 'assets-publish')
        self.assertEqual(step[1], 'publish_assets')
        self.assertNotIn('assets-publish', run_all.STEP_GROUPS['routine-build'])


if __name__ == '__main__':
    unittest.main()
