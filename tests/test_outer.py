import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from autor import adapters, engine
from autor.cli import initialize_preset
from autor.config import validate
from autor.presets import demo_config, harbor_preset
from autor.process import run_process
from autor.storage import AutorError, digest, locked, manifest, read_json, write_json


class OuterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "run"
        self.config = demo_config()

    def init(self, config=None):
        return initialize_preset(self.root, config or self.config)

    def run_outer(self, steps=None):
        return engine.Experiment(self.root).run(steps)

    def test_branching_creates_siblings_and_exports_complete_snapshot(self):
        self.init()
        state = self.run_outer()
        self.assertEqual(state["proposals_used"], 3)
        self.assertEqual(state["evaluations_used"], 4)
        self.assertEqual(state["candidates"][1]["parents"], ["c0000"])
        self.assertEqual(state["candidates"][2]["parents"], ["c0000"])
        self.assertEqual(state["best"], "c0003")
        destination = self.base / "export"
        engine.export(self.root, destination)
        self.assertEqual(manifest(destination / "harness"), manifest(self.root / "candidates/c0003/harness"))
        self.assertTrue((destination / "provenance.json").is_file())

    def test_hill_climb_uses_current_best(self):
        self.init(demo_config("hill_climb"))
        state = self.run_outer()
        self.assertEqual(state["candidates"][2]["parents"], ["c0001"])
        self.assertEqual(state["candidates"][3]["parents"], ["c0002"])

    def test_resume_does_not_repeat_baseline_or_exceed_budget(self):
        self.init()
        state = self.run_outer(1)
        self.assertEqual(state["evaluations_used"], 2)
        self.assertEqual(state["stop_reason"], "step_limit")
        state = self.run_outer()
        self.assertEqual(state["evaluations_used"], 4)
        with patch.object(adapters, "evaluate", side_effect=AssertionError("must not rerun")):
            state = self.run_outer()
        self.assertEqual(state["proposals_used"], 3)

    def test_failed_call_is_charged_and_not_replayed(self):
        self.init()
        self.run_outer(0)
        with patch.object(adapters, "propose", side_effect=AutorError("provider offline")):
            state = self.run_outer(1)
        self.assertEqual(state["proposals_used"], 1)
        self.assertEqual(state["evaluations_used"], 1)
        self.assertEqual(state["candidates"][1]["status"], "error")
        self.assertEqual(self.run_outer()["proposals_used"], 3)

    def test_baseline_infrastructure_failure_is_not_a_zero_score(self):
        self.init()
        with patch.object(adapters, "evaluate", side_effect=AutorError("container unavailable")):
            state = self.run_outer()
        self.assertIsNone(state["best"])
        self.assertNotIn("score", state["candidates"][0])
        self.assertEqual(state["stop_reason"], "no_measured_seed")
        self.assertEqual(state["evaluations_used"], 1)

    def test_crash_recovery_keeps_reserved_budget(self):
        state = self.init()
        state["evaluations_used"] = 1
        state["candidates"][0]["status"] = "evaluating"
        state["active"] = {"kind": "evaluation", "candidate": "c0000", "started_at": time.time() - 2, "timeout": 10}
        write_json(self.root / "run.json", state)
        with patch.object(adapters, "evaluate", side_effect=AssertionError("must not replay")):
            state = self.run_outer()
        self.assertEqual(state["candidates"][0]["status"], "interrupted")
        self.assertEqual(state["evaluations_used"], 1)
        self.assertGreaterEqual(state["seconds_used"], 2)

    def test_completed_result_recovers_without_reevaluation(self):
        state = self.init()
        state["evaluations_used"] = 1
        state["candidates"][0]["status"] = "evaluating"
        write_json(self.root / "run.json", state)
        result = {"status": "ok", "tasks": [{"task_id": "fixture", "score": 0.7}]}
        write_json(self.root / "candidates/c0000/evaluation.json", result)
        with patch.object(adapters, "evaluate", side_effect=AssertionError("must not replay")):
            state = self.run_outer(0)
        self.assertEqual(state["best"], "c0000")
        self.assertEqual(state["candidates"][0]["score"], 0.7)

    def test_allowlist_and_traversal_rejected_before_evaluation(self):
        self.init()
        self.run_outer(0)
        for filename in ("runtime.json", "../run.json", "/tmp/escape"):
            with self.subTest(filename=filename), patch.object(adapters, "propose", return_value={
                "hypothesis": "bad", "edits": [{"path": filename, "content": "changed"}]
            }):
                state = self.run_outer(1)
                self.assertEqual(state["candidates"][-1]["status"], "rejected")
                self.assertEqual(state["evaluations_used"], 1)

    def test_required_instruction_preserved(self):
        self.init()
        with patch.object(adapters, "propose", return_value={
            "hypothesis": "bad", "edits": [{"path": "prompt.md", "content": "lost task"}]
        }):
            state = self.run_outer(1)
        self.assertEqual(state["candidates"][-1]["status"], "rejected")
        self.assertEqual(state["evaluations_used"], 1)

    def test_duplicate_and_noop_do_not_get_evaluated(self):
        self.init()
        self.run_outer(1)
        text = (self.root / "candidates/c0001/harness/prompt.md").read_text()
        with patch.object(adapters, "propose", return_value={
            "hypothesis": "duplicate", "edits": [{"path": "prompt.md", "content": text}]
        }):
            state = self.run_outer(1)
        self.assertEqual(state["candidates"][-1]["status"], "rejected")
        self.assertEqual(state["evaluations_used"], 2)

    def test_frozen_config_and_snapshot_are_checked(self):
        state = self.init()
        state["config"]["strategy"] = "hill_climb"
        write_json(self.root / "run.json", state)
        with self.assertRaisesRegex(AutorError, "Frozen config"):
            engine.load(self.root)
        state["config"]["strategy"] = "branching"
        write_json(self.root / "run.json", state)
        (self.root / "candidates/c0000/harness/prompt.md").write_text("tampered")
        with self.assertRaisesRegex(AutorError, "snapshot was modified"):
            engine.load(self.root)

    def test_outer_change_requires_new_experiment(self):
        self.init()
        with patch.object(engine, "outer_fingerprint", return_value="new"):
            with self.assertRaisesRegex(AutorError, "Outer code"):
                engine.load(self.root)

    def test_symlink_seed_rejected(self):
        seed = self.base / "seed"
        seed.mkdir()
        (seed / "prompt.md").symlink_to(self.base / "outside")
        with self.assertRaisesRegex(AutorError, "Symlinks"):
            engine.initialize(self.root, seed, self.config)

    def test_concurrent_writer_lock(self):
        self.init()
        with locked(self.root):
            with self.assertRaisesRegex(AutorError, "Another process"):
                with locked(self.root):
                    pass

    def test_coverage_change_does_not_promote(self):
        self.init()
        self.run_outer(0)
        with patch.object(adapters, "evaluate", return_value={
            "status": "ok", "tasks": [{"task_id": "different", "score": 100}]
        }):
            state = self.run_outer(1)
        self.assertEqual(state["best"], "c0000")
        self.assertEqual(state["candidates"][-1]["status"], "error")

    def test_evaluation_cap_prevents_unmeasurable_proposals(self):
        self.config["budget"]["max_evaluations"] = 1
        self.init()
        state = self.run_outer()
        self.assertEqual(state["proposals_used"], 0)
        self.assertEqual(state["stop_reason"], "evaluation_budget")

    def test_process_timeout_and_nonzero_exit(self):
        with self.assertRaisesRegex(AutorError, "timed out"):
            run_process([sys.executable, "-c", "import time; time.sleep(10)"], self.base,
                        self.base / "timeout", 0.05)
        with self.assertRaisesRegex(AutorError, "exited with 3"):
            run_process([sys.executable, "-c", "raise SystemExit(3)"], self.base,
                        self.base / "error", 5)

    def test_command_evaluator_protocol_without_shell(self):
        script = self.base / "evaluator.py"
        script.write_text("import json,sys\nfrom pathlib import Path\n"
                          "assert Path(sys.argv[1]).is_dir()\n"
                          "Path(sys.argv[2]).write_text(json.dumps({'status':'ok','tasks':[{'task_id':'x','score':0.25}]}))\n")
        self.init()
        config = copy.deepcopy(self.config)
        config["evaluator"] = {"kind": "command", "argv": [sys.executable, str(script), "{harness}", "{result}"]}
        result = adapters.evaluate(config, self.root / "candidates/c0000/harness", self.base / "eval", 5)
        self.assertEqual(result["score"], 0.25)

    def test_real_codex_protocol_with_fake_executable(self):
        self.init()
        fake = self.base / "fake-codex"
        fake.write_text("#!" + sys.executable + "\nimport sys,json\nfrom pathlib import Path\n"
                        "assert '--sandbox' in sys.argv and 'read-only' in sys.argv\n"
                        "assert 'fixed outer loop' in sys.stdin.read()\n"
                        "out=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
                        "out.write_text(json.dumps({'hypothesis':'test','edits':[]}))\n")
        fake.chmod(0o755)
        config = copy.deepcopy(self.config)
        config["proposer"] = {"kind": "codex", "model": "test-model", "executable": str(fake)}
        result = adapters.propose(config, self.root / "candidates/c0000/harness", {"proposal_number": 1}, self.base / "propose", 5)
        self.assertEqual(result["hypothesis"], "test")
        self.assertTrue((self.base / "propose/logs/stdout.log").exists())


class EvaluationTests(unittest.TestCase):
    def test_nonfinite_empty_duplicate_and_error_results_rejected(self):
        invalid = [
            {"status": "error", "error": "broken"},
            {"status": "ok", "tasks": []},
            {"status": "ok", "tasks": [{"task_id": "x", "score": float("nan")}]},
            {"status": "ok", "tasks": [{"task_id": "x", "score": True}]},
            {"status": "ok", "tasks": [{"task_id": "x", "score": 1}] * 2},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(AutorError):
                adapters.normalize_result(value)

    def test_aggregate_recomputed_from_task_scores(self):
        r = adapters.normalize_result({"status": "ok", "score": 999, "tasks": [
            {"task_id": "x", "score": 0}, {"task_id": "y", "score": 1}]})
        self.assertEqual(r["score"], 0.5)

    def test_harbor_complete_coverage_and_missing_rewards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = {"task_names": ["task-a", "task-b"], "attempts": 1, "metric": "reward"}
            write_json(root / "result.json", {"stats": {"n_errored_trials": 0}})
            for name in settings["task_names"]:
                write_json(root / name / "result.json", {"task_name": name, "exception_info": None,
                           "task_checksum": name, "verifier_result": {"rewards": {"reward": 1}}})
            self.assertEqual(adapters.parse_harbor(root, settings)["score"], 1)
            (root / "task-b/result.json").unlink()
            with self.assertRaisesRegex(AutorError, "coverage mismatch"):
                adapters.parse_harbor(root, settings)
            write_json(root / "task-b/result.json", {"task_name": "task-b", "verifier_result": {"rewards": {}}})
            with self.assertRaisesRegex(AutorError, "Missing numeric"):
                adapters.parse_harbor(root, settings)

    def test_invalid_config_fails_before_work(self):
        for key, value in (("max_seconds", float("inf")), ("max_proposals", -1), ("max_evaluations", 0)):
            c = demo_config()
            c["budget"][key] = value
            with self.subTest(key=key), self.assertRaises(AutorError):
                validate(c)


if __name__ == "__main__":
    unittest.main()
