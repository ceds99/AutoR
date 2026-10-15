import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from scripts import bos_preflight as bos


class BOSPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = []
        active = {"state": "READY", "availability_state": "ACTIVE"}
        self.responses = {
            "/health/ready": {"status": "ready"},
            "/version": {"version": "fixture-only"},
            "/openapi.json": {"components": {"schemas": {"AgentRunSpec": {"properties": {
                "agent_release_ref": {}, "run_profile_ref": {}}}}},
                "paths": {"/v1/evaluation-runs": {}, "/v1/agent-releases": {}}},
            "/v1/execution-defaults": {"run_profile_ref": "run_profiles/p1", "harness_release_ref": "harness_releases/h1"},
            "/v1/run-profiles/p1": {"ref": "run_profiles/p1", "harness_release_ref": "harness_releases/h1", "model": {"name": "fixture"}},
            "/v1/harness-releases/h1": {"ref": "harness_releases/h1", "harness_type": "ma", **active},
            "/v1/agent-releases": {"agent_releases": [{"ref": "agent_releases/a1", "name": "fixture", "package_digest": "sha256:fixture", **active}]},
            "/v1/agent-releases/a1": {"ref": "agent_releases/a1", "package_digest": "sha256:fixture", **active},
            "/v1/case-versions/c1": {"ref": "case_versions/c1", **active},
        }
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.paths.append(self.path)
                body = fixture.responses.get(urlsplit(self.path).path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def probe(self, **kwargs):
        return bos.run(self.url, self.root / "evidence", **kwargs)

    def test_live_http_discovery_is_readonly_and_not_evaluation(self):
        report = self.probe()
        self.assertEqual(report["status"], "discovered")
        self.assertFalse(report["evaluation_ready"])
        self.assertEqual(report["remote_writes"], 0)
        self.assertEqual(report["model_calls"], 0)
        self.assertNotIn("selected_agent_release", report)
        self.assertTrue(any("Explicit AgentRelease" in gap for gap in report["missing"]))
        self.assertFalse(any("case" in path for path in self.paths))
        records = json.loads((self.root / "evidence/requests.json").read_text())
        self.assertEqual(len(records), 7)
        self.assertTrue(all(r["method"] == "GET" and len(r["sha256"]) == 64 for r in records))

    def test_explicit_resources_do_not_imply_candidate_activation(self):
        report = self.probe(agent_ref="agent_releases/a1", case_refs=["case_versions/c1"])
        self.assertEqual(report["status"], "discovered")
        self.assertEqual(len(report["search_cases"]), 1)
        self.assertTrue(any("runtime activation" in gap for gap in report["missing"]))
        self.assertFalse(report["evaluation_ready"])

    def test_mismatched_profile_stops_before_agent_selection(self):
        self.responses["/v1/run-profiles/p1"]["harness_release_ref"] = "harness_releases/other"
        report = self.probe()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(any("agent-release" in path for path in self.paths))

    def test_inactive_case_and_missing_package_digest_are_reported(self):
        self.responses["/v1/case-versions/c1"]["availability_state"] = "INACTIVE"
        del self.responses["/v1/agent-releases/a1"]["package_digest"]
        report = self.probe(agent_ref="agent_releases/a1", case_refs=["case_versions/c1"])
        self.assertTrue(any("package digest" in gap for gap in report["missing"]))
        self.assertTrue(any("CaseVersion not" in gap for gap in report["missing"]))

    def test_bad_refs_and_duplicate_cases_make_no_requests(self):
        for index, cases in enumerate((["case_versions/../secret"], ["case_versions/c1"] * 2)):
            report = bos.run(self.url, self.root / str(index), case_refs=cases)
            self.assertEqual(report["status"], "blocked")
        self.assertEqual(self.paths, [])

    def test_pagination_loop_is_bounded(self):
        self.responses["/v1/agent-releases"]["page"] = {"next_page_token": "again"}
        report = self.probe()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Repeated", report["error"])
        self.assertEqual(len(self.paths), 8)

    def test_http_failure_preserves_receipt_without_false_score(self):
        del self.responses["/health/ready"]
        report = self.probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["request_count"], 1)
        self.assertNotIn("score", report)
        self.assertTrue((self.root / "evidence/report.json").is_file())

    def test_changed_schema_shape_preserves_raw_evidence(self):
        self.responses["/openapi.json"]["components"] = None
        report = self.probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error"], "Unexpected deployment response shape")
        self.assertTrue((self.root / "evidence/response-003.json").is_file())

    def test_evidence_cannot_be_overwritten(self):
        self.probe()
        with self.assertRaisesRegex(bos.ProbeError, "new discovery"):
            self.probe()

    def test_service_origin_and_budget_are_explicit(self):
        for url in ("https://user:secret@host", "https://host?token=secret", "file:///tmp", "https://host/path"):
            with self.assertRaises(bos.ProbeError):
                bos.Client(url, self.root)
        for budget in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(bos.ProbeError):
                bos.Client(self.url, self.root, budget=budget)


if __name__ == "__main__":
    unittest.main()
