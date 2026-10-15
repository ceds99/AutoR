"""Read-only BOS discovery. This is not an evaluator or a candidate publisher.

Based on the user-supplied bos-exec service contract (2026-10-14). Fetch the
deployment's OpenAPI before implementing writes; never assume its default host.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ProbeError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resource_id(ref, kind):
    if not isinstance(ref, str) or not re.fullmatch(re.escape(kind) + r"/[A-Za-z0-9._-]+", ref):
        raise ProbeError("Expected an explicit " + kind + "/<id> reference")
    return ref.split("/", 1)[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Client:
    def __init__(self, base_url, root, timeout=5, budget=45):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ProbeError("Provide an HTTP(S) service origin without credentials, query or path")
        if any(not math.isfinite(v) or v <= 0 for v in (timeout, budget)):
            raise ProbeError("Timeout and budget must be positive finite seconds")
        self.base_url = base_url.rstrip("/")
        self.root = root
        self.timeout = timeout
        self.deadline = time.monotonic() + budget
        # Direct BOS access only; never alter process-wide proxy settings.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.requests = []

    def get(self, path):
        if not path.startswith("/") or path.startswith("//"):
            raise ProbeError("Expected a relative API path")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("Discovery time budget exhausted")
        record = {"method": "GET", "path": path}
        self.requests.append(record)
        started = time.monotonic()
        try:
            with self.opener.open(Request(self.base_url + path, method="GET"),
                                  timeout=min(self.timeout, remaining)) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                record["http_status"] = response.status
            if len(raw) > 16 * 1024 * 1024:
                raise ProbeError("Discovery response exceeds 16 MiB")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ProbeError("Expected a JSON object")
            filename = "response-{:03d}.json".format(len(self.requests))
            (self.root / filename).write_bytes(raw)
            record.update(file=filename, sha256=hashlib.sha256(raw).hexdigest(), status="ok")
            return value
        except HTTPError as exc:
            record.update(status="http_error", http_status=exc.code)
            raise ProbeError("HTTP {} at {}".format(exc.code, path)) from None
        except (URLError, OSError, ValueError) as exc:
            # Do not echo arbitrary response bodies or credential-bearing URLs.
            record.update(status="connection_or_response_error", error_type=type(exc).__name__)
            raise ProbeError("{} at {}".format(type(exc).__name__, path)) from None
        except ProbeError:
            record["status"] = "invalid_response"
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            save(self.root / "requests.json", self.requests)


def active(resource):
    return resource.get("state") == "READY" and resource.get("availability_state") == "ACTIVE"


def discover(client, agent_ref=None, case_refs=()):
    """Inspect only supplied cases; discovery must not enumerate a held-out set."""
    report = {"schema_version": 1, "status": "blocked", "remote_writes": 0,
              "model_calls": 0, "evaluation_ready": False, "missing": []}
    if agent_ref:
        resource_id(agent_ref, "agent_releases")
    if len(case_refs) > 6 or len(set(case_refs)) != len(case_refs):
        raise ProbeError("Choose at most six unique search CaseVersion references")
    for ref in case_refs:
        resource_id(ref, "case_versions")
    health = client.get("/health/ready")
    version = client.get("/version")
    schema = client.get("/openapi.json")
    report.update(service_version=version, health=health,
                  agent_run_fields=sorted(schema.get("components", {}).get("schemas", {})
                                          .get("AgentRunSpec", {}).get("properties", {})),
                  relevant_api_paths=sorted(p for p in schema.get("paths", {})
                                            if any(s in p for s in ("agent-release", "harness-", "evaluation-run", "upload", "trial"))))
    if health.get("status") != "ready":
        report["missing"].append("API readiness")
        return report
    pointer = client.get("/v1/execution-defaults")
    profile = client.get("/v1/run-profiles/" + resource_id(pointer.get("run_profile_ref"), "run_profiles"))
    harness = client.get("/v1/harness-releases/" + resource_id(pointer.get("harness_release_ref"), "harness_releases"))
    report.update(run_profile=profile, harness_release=harness)
    if (profile.get("ref") != pointer["run_profile_ref"]
            or harness.get("ref") != pointer["harness_release_ref"]
            or profile.get("harness_release_ref") != pointer["harness_release_ref"] or not active(harness)):
        report["missing"].append("Consistent Profile and READY/ACTIVE Harness")
        return report
    if agent_ref:
        agent = client.get("/v1/agent-releases/" + resource_id(agent_ref, "agent_releases"))
        report["selected_agent_release"] = agent
        if agent.get("ref") != agent_ref or not active(agent) or not agent.get("package_digest"):
            report["missing"].append("Selected AgentRelease identity, readiness or package digest")
    else:
        candidates, seen, token = [], set(), None
        for _ in range(20):
            query = {"limit": 200, "state": "READY", "availability_state": "ACTIVE"}
            if token:
                query["page_token"] = token
            page = client.get("/v1/agent-releases?" + urlencode(query))
            for item in page["agent_releases"]:
                if active(item):
                    candidates.append({k: item.get(k) for k in ("ref", "name", "version", "package_format", "package_digest")})
            token = (page.get("page") or {}).get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ProbeError("Repeated AgentRelease page token")
            seen.add(token)
        else:
            raise ProbeError("AgentRelease page limit exceeded")
        report["agent_release_candidates"] = candidates
        report["missing"].append("Explicit AgentRelease selection; no latest/default package selected")
    report["search_cases"] = []
    for ref in case_refs:
        case = client.get("/v1/case-versions/" + resource_id(ref, "case_versions"))
        report["search_cases"].append(case)
        if case.get("ref") != ref or not active(case):
            report["missing"].append("Search CaseVersion not READY/ACTIVE: " + ref)
    if not case_refs:
        report["missing"].append("Explicit search CaseVersions (no case enumeration)")
    # Even ready resources do not prove that AutoR prompt snapshots can be installed.
    report["missing"].extend([
        "Verify candidate packaging/publishing contract against this deployment",
        "Bind candidate digest to resolved_identity and actual runtime activation",
        "Run a bounded full evaluation and verify complete valid Trial evidence",
    ])
    report["status"] = "discovered"
    return report


def run(base_url, output, agent_ref=None, case_refs=(), timeout=5, budget=45):
    root = Path(output).resolve()
    if root.exists():
        raise ProbeError("Choose a new discovery directory; preserve existing evidence")
    root.mkdir(parents=True, mode=0o700)
    client = Client(base_url, root, timeout, budget)
    save(root / "input.json", {"base_url": client.base_url, "agent_release_ref": agent_ref,
                               "case_version_refs": list(case_refs), "timeout": timeout, "budget": budget})
    try:
        report = discover(client, agent_ref, case_refs)
    except (ProbeError, KeyError, TypeError, AttributeError) as exc:
        report = {"schema_version": 1, "status": "blocked", "evaluation_ready": False,
                  "remote_writes": 0, "model_calls": 0,
                  "error": str(exc) if isinstance(exc, ProbeError) else "Unexpected deployment response shape"}
    report["request_count"] = len(client.requests)
    report["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save(root / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="New local evidence directory")
    parser.add_argument("--base-url", default=os.environ.get("BOS_BASE_URL"))
    parser.add_argument("--agent-release-ref")
    parser.add_argument("--case-version-ref", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--budget", type=float, default=45)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("Set --base-url or BOS_BASE_URL explicitly")
    try:
        report = run(args.base_url, args.output, args.agent_release_ref,
                     args.case_version_ref, args.timeout, args.budget)
    except ProbeError as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "evaluation_ready": False,
                      "report": str(Path(args.output).resolve() / "report.json")}, ensure_ascii=False))
    return 0 if report["status"] == "discovered" else 2


if __name__ == "__main__":
    raise SystemExit(main())
