import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import terminal_bench_pilot as pilot
from autor.storage import AutorError, read_json, write_json


class TerminalBenchPilotTests(unittest.TestCase):
    def test_prepare_is_search_only_and_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "openai/fixture", "0.153.4", "a" * 64)
            job = read_json(root / "search-job.json")
            self.assertEqual([t["path"] for t in job["tasks"]], pilot.SEARCH_TASKS)
            self.assertTrue(all(t["git_commit_id"] == pilot.REVISION for t in job["tasks"]))
            self.assertEqual(job["datasets"], [])
            self.assertEqual(job["retry"]["max_retries"], 0)
            self.assertEqual(pilot.load(root)["mode"], "baseline-only")
            self.assertNotIn("holdout", (root / "harness/prompt.md").read_text())

    def test_missing_environment_does_not_reserve_a_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64)
            with patch.object(pilot, "preflight", return_value={"ready": False}):
                with self.assertRaisesRegex(AutorError, "No evaluation started"):
                    pilot.run(root)
            self.assertFalse((root / "started.json").exists())

    def test_changed_contract_and_uncertain_replay_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64)
            (root / "started.json").write_text("{}")
            with patch.object(pilot, "preflight", return_value={"ready": True}), patch.object(pilot, "run_process") as process:
                with self.assertRaises(FileExistsError):
                    pilot.run(root)
                process.assert_not_called()
            job = read_json(root / "search-job.json")
            job["tasks"].pop()
            write_json(root / "search-job.json", job)
            with self.assertRaisesRegex(AutorError, "job changed"):
                pilot.load(root)

    @unittest.skipUnless(importlib.util.find_spec("harbor"), "install Harbor 0.24.0")
    def test_pilot_validates_with_real_harbor_schema(self):
        from harbor.models.job.config import JobConfig
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "openai/fixture", "0.153.4", "a" * 64)
            parsed = JobConfig.model_validate(read_json(root / "search-job.json"))
            self.assertEqual(len(parsed.tasks), 4)
            self.assertEqual(parsed.agents[0].kwargs["reasoning_effort"], "low")
