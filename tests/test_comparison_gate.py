import json
from pathlib import Path
import tempfile
import unittest
from autor.storage import AutorError, write_json
from scripts.compare_search import natural_failure


class NaturalFailureGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.trial=Path(self.temp.name)
        (self.trial/'agent').mkdir();(self.trial/'verifier').mkdir()
        self.row={'task_name':'fixture','trial_name':'fixture__id','task_checksum':'checksum',
                  'verifier_result':{'rewards':{'reward':0}},'exception_info':None,
                  'agent_execution':{'finished_at':'2026-10-16T00:00:00Z'}}
        write_json(self.trial/'result.json',self.row)
        (self.trial/'agent/codex.txt').write_text(json.dumps({'type':'turn.completed'})+'\n')
        (self.trial/'verifier/test-stdout.txt').write_text('collected 2 items\nFAILED test_output - AssertionError\n')

    def test_complete_scored_failure_is_eligible(self):
        result=natural_failure(self.trial)
        self.assertEqual(result['score'],0)
        self.assertEqual(len(result['verifier_stdout_sha256']),64)

    def test_ceiling_or_infrastructure_error_cannot_unlock_search(self):
        for score,error in [(1,None),(0,{'exception_type':'NetworkConnectionError'}),(None,None)]:
            self.row['verifier_result']['rewards']['reward']=score;self.row['exception_info']=error
            write_json(self.trial/'result.json',self.row)
            with self.assertRaises(AutorError):natural_failure(self.trial)

    def test_incomplete_model_turn_is_not_a_natural_failure(self):
        (self.trial/'agent/codex.txt').write_text('{"type":"turn.started"}\n')
        with self.assertRaisesRegex(AutorError,'completed agent turn'):natural_failure(self.trial)

    def test_verifier_install_error_is_not_a_natural_failure(self):
        (self.trial/'verifier/test-stdout.txt').write_text('FAILED to download dependencies\n')
        with self.assertRaisesRegex(AutorError,'executed scoring tests'):natural_failure(self.trial)

    def test_numeric_zero_from_rosetta_verifier_failure_does_not_unlock(self):
        (self.trial/'verifier/test-stdout.txt').write_text(
            'collected 3 items\nrosetta error: Unable to open /proc/self/exe: 2\n'
            'FAILED test_image_similarity - subprocess.CalledProcessError\n')
        with self.assertRaisesRegex(AutorError,'infrastructure failure'):natural_failure(self.trial)
