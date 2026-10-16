import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import compare_search
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


class BaselineOrchestrationTests(unittest.TestCase):
    def run_gate(self, first):
        state={'proposals_used':0,'candidates':[{'status':'evaluated','hash':'same','score':0,
            'task_scores':[{'task_id':'fixture','score':0,'trial_dir':'unused'}]}],
            'task_checksums':[['fixture','checksum']]}
        import copy
        initial=copy.deepcopy(state);initial.update(first)
        with tempfile.TemporaryDirectory() as temp, patch.object(compare_search,'check',return_value={
                'failure_gate':{'task':'fixture','task_checksum':'checksum'}}), patch.object(
                compare_search.engine,'load',return_value=state), patch.object(
                compare_search.engine,'Experiment') as experiment, patch.object(
                compare_search,'natural_failure'), patch.object(compare_search,'summary',return_value={}):
            experiment.return_value.run.side_effect=[initial,state,state]
            result=compare_search.baselines(Path(temp))
            return result, experiment.call_count

    def test_interrupt_does_not_start_next_arm(self):
        gate,calls=self.run_gate({'stop_reason':'interrupted'})
        self.assertFalse(gate['allowed']);self.assertEqual(calls,1)
        self.assertEqual(gate['not_run_arms'],['B','C'])

    def test_ceiling_does_not_start_next_arm(self):
        gate,calls=self.run_gate({'candidates':[{'status':'evaluated','hash':'same','score':1,
            'task_scores':[{'task_id':'fixture','score':1}]}]})
        self.assertFalse(gate['allowed']);self.assertEqual(calls,1)

    def test_valid_reproduction_runs_all_three_arms(self):
        gate,calls=self.run_gate({})
        self.assertTrue(gate['allowed']);self.assertEqual(calls,3)
        self.assertEqual(gate['not_run_arms'],[])


class OuterCliPreflightTests(unittest.TestCase):
    def test_short_and_full_versions_store_the_actual_full_output(self):
        from types import SimpleNamespace
        with patch.object(compare_search.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='codex-cli 1.2.3\n')):
            for pin in ['1.2.3','codex-cli 1.2.3']:
                self.assertEqual(compare_search.verify_outer_cli('fixture',pin),'codex-cli 1.2.3')

    def test_wrong_version_is_rejected(self):
        from types import SimpleNamespace
        with patch.object(compare_search.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='codex-cli 1.2.4\n')):
            with self.assertRaises(AutorError):compare_search.verify_outer_cli('fixture','1.2.3')

    def test_adopted_preflight_cost_does_not_bypass_seed_state_check(self):
        state={'proposals_used':2,'config':{'proposer':{'executable':'fixture','cli_version':'codex-cli 1.2.3'}}}
        with tempfile.TemporaryDirectory() as temp, patch.object(compare_search,'check'), patch.object(
                compare_search.engine,'load',return_value=state), patch.object(compare_search,'verify_outer_cli'), patch.object(
                compare_search.engine,'Experiment') as experiment:
            write_json(Path(temp)/'baseline-gate.json',{'allowed':True,'seed_results':{'A':{'budget':{'proposals_used':2}}},
                'seed_state_hashes':{'A':'mismatched'}})
            with self.assertRaisesRegex(AutorError,'Seed state changed'):compare_search.search(temp)
            experiment.assert_not_called()
