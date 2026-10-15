import importlib.util
import os
import subprocess
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

    def test_runtime_override_is_explicit_readonly_and_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64, bounded_apt=True)
            job = read_json(root / "search-job.json")
            self.assertTrue(job["environment"]["mounts"][0]["read_only"])
            self.assertEqual(job["environment"]["mounts"][0]["target"], "/etc/apt/apt.conf.d/99autor-pilot-network")
            self.assertIn("runtime/apt-network.conf", pilot.load(root)["runtime_overrides"])
            (root / "runtime/apt-network.conf").write_text("changed")
            with self.assertRaisesRegex(AutorError, "runtime override changed"):
                pilot.load(root)

    def test_node_runtime_links_are_bounded_and_contents_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "node"
            source.mkdir()
            (source / "node").write_text("fixture")
            (source / "alias").symlink_to("node")
            root = base / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64, node_runtime=source)
            self.assertIn("runtime/node", pilot.load(root)["runtime_overrides"])
            self.assertNotIn("PATH", read_json(root / "search-job.json")["environment"]["env"])
            (root / "runtime/node/node").write_text("changed")
            with self.assertRaisesRegex(AutorError, "runtime override changed"):
                pilot.load(root)
            (source / "escape").symlink_to(base)
            with self.assertRaisesRegex(AutorError, "symlink escapes"):
                pilot.runtime_digest(source)

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
            self.assertEqual(parsed.n_concurrent_trials, 1)
            self.assertEqual(parsed.retry.max_retries, 0)
            self.assertEqual(parsed.agents[0].max_timeout_sec, 180)
            self.assertTrue(parsed.environment.delete)

    def test_missing_compose_blocks_before_any_evaluation(self):
        def probe(argv, **kwargs):
            if argv[:3] == ["docker", "compose", "version"]:
                return subprocess.CompletedProcess(argv, 1, "", "plugin missing")
            return subprocess.CompletedProcess(argv, 0, "0.24.0" if argv[0] == "harbor" else "linux", "")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture"}, clear=True), patch.object(
                    pilot.shutil, "which", return_value="/fixture"), patch.object(
                    pilot.subprocess, "run", side_effect=probe):
                checks = pilot.preflight(root)
                self.assertFalse(checks["ready"])
                self.assertFalse(next(c for c in checks["checks"] if c["check"] == "Docker Compose")["ok"])
                with self.assertRaisesRegex(AutorError, "No evaluation started"):
                    pilot.run(root)
            self.assertFalse((root / "started.json").exists())
