"""Restrict proposer command access to its staged inputs and verify enforcement.

This controls Codex local commands, not the trusted controller or model service.
It does not establish a separate evaluator service or erase model pretraining.
"""

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

from .storage import AutorError, write_json


PROFILE = "autor-proposal"


def config_args(work):
    filesystem = {":minimal": "read", str(Path(work).resolve()): "read"}
    if sys.platform == "darwin" and Path("/Library/Developer/CommandLineTools").is_dir():
        filesystem["/Library/Developer/CommandLineTools"] = "read"
    value = "{" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in filesystem.items()) + "}"
    return ["-c", 'default_permissions="' + PROFILE + '"',
            "-c", "permissions." + PROFILE + ".filesystem=" + value,
            "-c", "permissions." + PROFILE + ".network.enabled=false",
            "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0"]


def verify_boundary(executable, work, timeout=15):
    """Run a local canary, never a model call, using the same permission rules.

Fail closed if the CLI/platform cannot enforce the boundary. A loopback listener
makes the network assertion distinguish denial from an unavailable destination.
"""
    work = Path(work).resolve()
    if sys.platform not in ("darwin", "linux"):
        raise AutorError("Proposer isolation probe currently supports macOS and Linux only")
    direct = Path("/Library/Developer/CommandLineTools/usr/bin/python3")
    python = str(direct) if sys.platform == "darwin" and direct.is_file() else "/usr/bin/python3"
    with tempfile.TemporaryDirectory(prefix=".boundary-", dir=work) as allowed, tempfile.TemporaryDirectory(
            prefix=".boundary-", dir=work.parent) as forbidden, socket.socket() as listener:
        inside = Path(allowed) / "visible"
        outside = Path(forbidden) / "hidden"
        link = Path(allowed) / "escape"
        inside.write_text("VISIBLE", encoding="utf-8")
        outside.write_text("CANARY_ONLY", encoding="utf-8")
        link.symlink_to(outside)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        code = '''import json, pathlib, socket, sys
inside, outside, link, port = sys.argv[1:]
def readable(path):
    try:
        pathlib.Path(path).read_bytes()
        return True
    except OSError:
        return False
try:
    pathlib.Path(inside).write_text("MUTATED")
    write_denied = False
except OSError:
    write_denied = True
s = socket.socket()
s.settimeout(1)
try:
    network_denied = s.connect_ex(("127.0.0.1", int(port))) != 0
except OSError:
    network_denied = True
s.close()
print(json.dumps({"input_readable": readable(inside), "outside_denied": not readable(outside),
                  "symlink_escape_denied": not readable(link), "write_denied": write_denied,
                  "command_network_denied": network_denied}))
'''
        argv = [executable, "sandbox", "-P", PROFILE, "-C", str(work), *config_args(work),
                python, "-I", "-c", code, str(inside), str(outside), str(link), str(listener.getsockname()[1])]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            checks = json.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise AutorError("Proposer isolation probe failed; no model request made") from error
        names = {"input_readable", "outside_denied", "symlink_escape_denied", "write_denied", "command_network_denied"}
        if set(checks) != names or not all(v is True for v in checks.values()):
            raise AutorError("Proposer permission boundary is unavailable; no model request made")
    receipt = {"profile": PROFILE, "checks": checks, "model_calls": 0,
               "scope": "Local sandboxed commands only. Controller and model/authentication service traffic are outside this boundary."}
    write_json(work / "isolation.json", receipt)
    return receipt
