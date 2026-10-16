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
    def test_agent_platform_is_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64, docker_platform="linux/amd64", keep_images=True)
            self.assertFalse(read_json(root / "search-job.json")["environment"]["delete"])
            overlay = root / "runtime/docker-platform.yaml"
            self.assertEqual(read_json(root / "search-job.json")["environment"]["extra_docker_compose"], [str(overlay.resolve())])
            pilot.load(root)
            overlay.write_text("services: {}")
            with self.assertRaisesRegex(AutorError, "runtime override changed"):
                pilot.load(root)

    def test_dependency_cache_is_frozen_and_privately_copied(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            node = base / "node"; node.mkdir(); (node / "node").write_text("fixture")
            cache = base / "cache"; cache.mkdir(); (cache / "wheel.txt").write_text("clean")
            root = base / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64, node_runtime=node, dependency_cache=cache)
            staged = root / "runtime/uv-cache"
            mount = next(m for m in read_json(root / "search-job.json")["environment"]["mounts"]
                         if m["target"] == "/opt/autor-uv-cache")
            self.assertEqual(mount["target"], "/opt/autor-uv-cache")
            self.assertTrue(mount["read_only"])
            profile = (root / "runtime/node-profile.sh").read_text()
            for index in (1, 2):
                private = base / ("container-" + str(index))
                local_profile = base / ("profile-" + str(index))
                local_profile.write_text(profile.replace("/opt/autor-uv-cache", str(staged)).replace("/tmp/autor-uv-cache", str(private)))
                subprocess.run(["bash", "-c", 'test "$(cat "$UV_CACHE_DIR/wheel.txt")" = clean && printf changed > "$UV_CACHE_DIR/wheel.txt"'],
                               env={**os.environ, "BASH_ENV": str(local_profile)}, check=True)
            self.assertEqual((staged / "wheel.txt").read_text(), "clean")
            pilot.load(root)
            (staged / "wheel.txt").write_text("drift")
            with self.assertRaisesRegex(AutorError, "runtime override changed"):
                pilot.load(root)

    def test_network_proxy_is_container_only_and_preserves_local_endpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "runtime"
            source.mkdir()
            (source / "node").write_text("fixture")
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64, node_runtime=source,
                          network_proxy="http://proxy.example:3128")
            profile = (root / "runtime/node-profile.sh").read_text()
            self.assertIn('export HTTPS_PROXY=http://proxy.example:3128', profile)
            self.assertIn('export NO_PROXY=localhost,127.0.0.1,::1', profile)
            self.assertEqual(read_json(root / "search-job.json")["environment"]["env"],
                             {"BASH_ENV": "/etc/profile.d/autor-node.sh"})
            (root / "runtime/node-profile.sh").write_text(profile.replace("3128", "3129"))
            with self.assertRaisesRegex(AutorError, "runtime override changed"):
                pilot.load(root)

    def test_apt_proxy_is_explicit_frozen_and_has_no_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            pilot.prepare(root, "fixture", "0.153.4", "a" * 64, bounded_apt=True,
                          apt_proxy="http://proxy.example:3128")
            config = (root / "runtime/apt-network.conf").read_text()
            self.assertIn('Acquire::http::Proxy "http://proxy.example:3128";', config)
            self.assertIn('Acquire::https::Proxy "http://proxy.example:3128";', config)
            (root / "runtime/apt-network.conf").write_text(config.replace("3128", "3129"))
            with self.assertRaisesRegex(AutorError, "runtime override changed"):
                pilot.load(root)
            for index, proxy in enumerate(["http://user:secret@proxy", "http://proxy?token=x", "file:///tmp", "http://proxy\n"]):
                with self.subTest(proxy=proxy), self.assertRaises(AutorError):
                    pilot.prepare(Path(temp) / str(index), "fixture", "0.153.4", "a" * 64,
                                  bounded_apt=True, apt_proxy=proxy)

    def test_explicit_search_selection_and_budgets_are_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            meta = pilot.prepare(root, "fixture", "0.153.4", "a" * 64,
                                 task_names=["regex-log", "db-wal-recovery"], agent_timeout=300, max_seconds=1800)
            job = read_json(root / "search-job.json")
            self.assertEqual([t["path"] for t in job["tasks"]], ["regex-log", "db-wal-recovery"])
            self.assertEqual(job["agents"][0]["max_timeout_sec"], 300)
            self.assertEqual(meta["max_seconds"], 1800)
            job["tasks"].reverse()
            write_json(root / "search-job.json", job)
            with self.assertRaisesRegex(AutorError, "job changed"):
                pilot.load(root)

    def test_invalid_search_selection_or_budget_rejected(self):
        cases = [{"task_names": x} for x in ([], ["x", "x"], ["../x"], ["fix-*"], ["x"] * 7)]
        cases += [{"agent_timeout": 0}, {"max_seconds": float("inf")}, {"agent_timeout": True}]
        cases += [{"task_revision": "main"}, {"task_prefix": "../tasks/"},
                  {"task_git_url": "https://user:secret@example.org/tasks.git"},
                  {"dataset_version": "latest"}]
        with tempfile.TemporaryDirectory() as temp:
            for index, kwargs in enumerate(cases):
                with self.subTest(kwargs=kwargs), self.assertRaises(AutorError):
                    pilot.prepare(Path(temp) / str(index), "fixture", "0.153.4", "a" * 64, **kwargs)

    def test_other_dataset_revision_and_prefix_are_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pilot"
            meta = pilot.prepare(root, "fixture", "0.153.4", "a" * 64,
                task_names=["example"], task_git_url="https://example.org/tasks.git",
                task_revision="b" * 40, task_prefix="tasks/", dataset_version="4.0.0", task_name_prefix="terminal-bench/")
            job = read_json(root / "search-job.json")
            self.assertEqual(job["tasks"], [{"path": "tasks/example", "git_url": "https://example.org/tasks.git",
                "git_commit_id": "b" * 40, "source": "terminal-bench-4.0.0-pilot"}])
            self.assertEqual(meta["evaluator"]["dataset_version"], "4.0.0")
            self.assertEqual(meta["evaluator"]["task_names"], ["terminal-bench/example"])
            self.assertEqual(pilot.load(root)["task_revision"], "b" * 40)

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
