"""Thin wrapper around the rclone binary."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


class RcloneError(Exception):
    """rclone is missing or a command failed."""


def find_rclone() -> Optional[str]:
    """Locate the rclone binary.

    Order: TIDYSYNC_RCLONE override -> PATH -> common install locations
    (winget often isn't on the PATH of a shell opened before install).
    """
    override = os.environ.get("TIDYSYNC_RCLONE")
    if override and Path(override).exists():
        return override

    found = shutil.which("rclone")
    if found:
        return found

    home = Path.home()
    patterns = [
        str(home / "AppData/Local/Microsoft/WinGet/Packages/Rclone.Rclone*/**/rclone.exe"),
        str(home / "AppData/Local/Microsoft/WinGet/Links/rclone.exe"),
        r"C:\Program Files\rclone\rclone.exe",
        r"C:\ProgramData\chocolatey\bin\rclone.exe",
        "/usr/bin/rclone", "/usr/local/bin/rclone", "/opt/homebrew/bin/rclone",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[-1]
    return None


def ensure_rclone() -> str:
    exe = find_rclone()
    if not exe:
        raise RcloneError(
            "rclone is not installed or not on PATH.\n"
            "Install it with:  winget install Rclone.Rclone\n"
            "or set TIDYSYNC_RCLONE to the full path of rclone.exe, then re-run."
        )
    return exe


def version() -> str:
    exe = ensure_rclone()
    out = subprocess.run([exe, "version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (out.stdout or "").strip().splitlines()[0] if out.stdout else ""


def list_remotes() -> List[str]:
    exe = ensure_rclone()
    out = subprocess.run([exe, "listremotes"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RcloneError(out.stderr.strip() or "rclone listremotes failed")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def check_remote(remote: str) -> None:
    """Raise RcloneError if the remote root cannot be listed."""
    exe = ensure_rclone()
    out = subprocess.run(
        [exe, "lsd", remote, "--max-depth", "1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RcloneError(
            f"Cannot reach remote '{remote}'. rclone said:\n{out.stderr.strip()}"
        )


def _filter_args(filters: List[str]) -> List[str]:
    args: List[str] = []
    for rule in filters:
        args += ["--filter", rule]
    return args


def lsjson(path: str, max_age: Optional[str] = None,
           filters: Optional[List[str]] = None, with_hash: bool = False) -> List[dict]:
    """List files under `path` (recursively, files only).

    With `max_age` set, only files newer than it are returned. With `with_hash`,
    each item includes a "Hashes" mapping. Item keys: Path, Size, ModTime, IsDir
    (+ Hashes when requested).
    """
    exe = ensure_rclone()
    cmd = [exe, "lsjson", "-R", "--files-only"]
    if max_age:
        cmd += ["--max-age", max_age]
    if with_hash:
        cmd += ["--hash"]
    cmd.append(path)
    cmd += _filter_args(filters or [])
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RcloneError(
            f"rclone lsjson failed for {path}:\n{out.stderr.strip()}"
        )
    try:
        return json.loads(out.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RcloneError(f"Could not parse lsjson output for {path}: {exc}")


def moveto(src: str, dst: str, dry_run: bool = False) -> Tuple[bool, str]:
    """Move a single file from src to dst (within one remote). Returns (ok, error)."""
    exe = ensure_rclone()
    cmd = [exe, "moveto", src, dst]
    if dry_run:
        cmd.append("--dry-run")
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return False, (out.stderr.strip() or "rclone moveto failed")
    return True, ""


def copyto(src: str, dst: str, extra: Optional[List[str]] = None,
           dry_run: bool = False) -> Tuple[bool, str]:
    """Copy a single file src -> dst (used for Google-doc export/upload)."""
    exe = ensure_rclone()
    cmd = [exe, "copyto", src, dst]
    if dry_run:
        cmd.append("--dry-run")
    if extra:
        cmd += extra
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return False, (out.stderr.strip() or "rclone copyto failed")
    return True, ""


def remote_type(remote: str) -> str:
    """Return the rclone backend type for a remote (e.g. 'drive', 'onedrive')."""
    exe = ensure_rclone()
    out = subprocess.run([exe, "config", "dump"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return ""
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    name = remote.split(":")[0]
    return (data.get(name) or {}).get("type", "")


@dataclass
class CopyResult:
    src: str
    dst: str
    created: List[str] = field(default_factory=list)   # paths copied as new
    updated: List[str] = field(default_factory=list)   # paths that replaced an existing file
    errors: List[str] = field(default_factory=list)    # human-readable error lines
    returncode: int = 0

    @property
    def transferred(self) -> List[str]:
        return self.created + self.updated


def _classify(msg: str) -> Optional[str]:
    m = msg.lower()
    if "copied (new)" in m:
        return "created"
    if "copied (replaced existing)" in m or "updated" in m or "copied (server-side" in m:
        return "updated"
    if m.startswith("copied"):
        return "updated"
    return None


def copy(
    src: str,
    dst: str,
    max_age: str,
    filters: List[str],
    dry_run: bool = False,
    extra: Optional[List[str]] = None,
) -> CopyResult:
    """Delta copy src -> dst: only files newer than max_age, never overwriting a
    newer destination (--update). Identical files are skipped by rclone."""
    exe = ensure_rclone()
    result = CopyResult(src=src, dst=dst)

    with tempfile.NamedTemporaryFile(
        "r", suffix=".log", delete=False, encoding="utf-8"
    ) as tf:
        log_path = Path(tf.name)

    cmd = [
        exe, "copy", src, dst,
        "--max-age", max_age,
        "--update",
        "--create-empty-src-dirs",
        "--use-json-log",
        "-v",
        "--log-file", str(log_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd += _filter_args(filters)
    if extra:
        cmd += extra

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    result.returncode = proc.returncode

    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        level = entry.get("level", "")
        msg = entry.get("msg", "")
        obj = entry.get("object", "")
        if level == "error":
            result.errors.append(f"{obj}: {msg}" if obj else msg)
            continue
        if not obj:
            continue
        kind = _classify(msg)
        if kind == "created":
            result.created.append(obj)
        elif kind == "updated":
            result.updated.append(obj)

    try:
        log_path.unlink()
    except OSError:
        pass

    if proc.returncode != 0 and not result.errors:
        result.errors.append(
            (proc.stderr or "rclone copy failed").strip().splitlines()[-1]
        )
    return result
