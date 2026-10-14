"""Bounded local commands, with process-group cancellation on macOS/Linux."""

import os
from pathlib import Path
import signal
import subprocess
import time

from .storage import AutorError


def run_process(argv, cwd, logs, timeout, stdin=None, env=None):
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    if timeout <= 0:
        raise AutorError("No execution time remaining")
    started = time.monotonic()
    with (logs / "stdout.log").open("w") as out, (logs / "stderr.log").open("w") as err:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, text=True,
                                       stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
        except OSError as e:
            raise AutorError("Cannot start {}: {}".format(argv[0], e)) from e
        try:
            process.communicate(stdin, timeout=timeout)
        except BaseException as e:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if isinstance(e, subprocess.TimeoutExpired):
                raise AutorError("Command timed out after {:.1f}s; see {}".format(timeout, logs)) from e
            raise
    if process.returncode:
        raise AutorError("Command exited with {}; see {}".format(process.returncode, logs))
    return time.monotonic() - started
