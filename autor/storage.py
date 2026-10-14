"""Small durable store. One writer per experiment, immutable harness snapshots."""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


class AutorError(Exception):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".write-", delete=False) as f:
        tmp = Path(f.name)
        try:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise AutorError("File paths must be nonempty relative POSIX paths")
    p = Path(value)
    if p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise AutorError("Unsafe relative path: " + value)
    return p


def manifest(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise AutorError("Harness must be a real directory")
    result = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise AutorError("Symlinks are not supported in harness snapshots: " + str(p))
        if p.is_file():
            result[p.relative_to(root).as_posix()] = {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "executable": bool(p.stat().st_mode & 0o111),
            }
        elif not p.is_dir():
            raise AutorError("Only regular files are supported: " + str(p))
    return result


def copy_harness(source, destination):
    manifest(source)  # Reject links before copytree follows anything.
    shutil.copytree(source, destination)
    if manifest(source) != manifest(destination):
        raise AutorError("Harness changed during snapshot creation")


@contextlib.contextmanager
def locked(root):
    root = Path(root)
    if not (root / "run.json").is_file():
        raise AutorError("Not an AutoR experiment: " + str(root))
    with (root / ".lock").open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise AutorError("Another process is using this experiment") from e
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def outer_fingerprint():
    root = Path(__file__).parent
    files = list(root.glob("*.py")) + list(root.glob("skills/*/SKILL.md"))
    return digest({p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                   for p in sorted(files)})
