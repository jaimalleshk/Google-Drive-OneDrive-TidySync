"""Per-cloud, content-hash duplicate detection with quarantine-for-review.

Duplicates are decided purely by content hash (NOT filename) within a SINGLE
remote — comparing hashes across clouds is meaningless (different algorithms).
The newest-modified copy in each group is kept; older copies are moved to a
quarantine folder for the user to review and delete. Files only; never folders.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tidysync import rclone
from tidysync.state import utcnow_iso

QUARANTINE_DIR = "_duplicates"

# Prefer strong, widely-available hashes first.
HASH_PREFERENCE = ["sha256", "sha1", "md5", "quickxor", "crc32", "blake3", "whirlpool"]


@dataclass
class DupGroup:
    hash_type: str
    hash_value: str
    kept: dict                       # the file we keep (newest)
    quarantined: List[dict] = field(default_factory=list)


@dataclass
class DedupeResult:
    remote: str
    scope_desc: str
    quarantine: str
    apply: bool
    started: str
    finished: str = ""
    duration_s: float = 0.0
    files_scanned: int = 0
    groups: List[DupGroup] = field(default_factory=list)
    skipped_no_hash: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def totals(self) -> Dict[str, int]:
        quarantined = sum(len(g.quarantined) for g in self.groups)
        reclaim = sum(int(f.get("Size") or 0) for g in self.groups for f in g.quarantined)
        return {
            "files_scanned": self.files_scanned,
            "duplicate_groups": len(self.groups),
            "duplicates": quarantined,
            "reclaimable_bytes": reclaim,
            "skipped_no_hash": len(self.skipped_no_hash),
            "errors": len(self.errors),
        }


def _best_hash(hashes: dict) -> Optional[Tuple[str, str]]:
    if not hashes:
        return None
    for name in HASH_PREFERENCE:
        val = hashes.get(name)
        if val:
            return name, val
    # fall back to any non-empty hash the backend offered
    for name, val in hashes.items():
        if val:
            return name, val
    return None


def _join(remote: str, rel: str) -> str:
    if remote.endswith(":"):
        return remote + rel
    return remote.rstrip("/") + "/" + rel


def _scan_paths(remote: str, folders: Optional[List[str]]) -> List[Tuple[str, str]]:
    """Return (scan_path, prefix) pairs. prefix is the folder relative to remote root."""
    if folders:
        return [(_join(remote, f), f) for f in folders]
    return [(remote, "")]


def _pick_canonical(files: List[dict]) -> Tuple[dict, List[dict]]:
    """Keep newest ModTime; tie-break by shortest path then alphabetical."""
    newest = max(f.get("ModTime", "") for f in files)
    tied = [f for f in files if f.get("ModTime", "") == newest]
    tied.sort(key=lambda f: (len(f["_full"]), f["_full"]))
    keep = tied[0]
    rest = [f for f in files if f is not keep]
    return keep, rest


def find_duplicates(remote: str, folders: Optional[List[str]] = None,
                    filters: Optional[List[str]] = None,
                    quarantine: str = QUARANTINE_DIR) -> DedupeResult:
    rclone.ensure_rclone()
    start = time.time()
    result = DedupeResult(
        remote=remote,
        scope_desc=("folders: " + ", ".join(folders)) if folders else "whole drive",
        quarantine=quarantine, apply=False, started=utcnow_iso(),
    )

    by_hash: Dict[Tuple[str, str], List[dict]] = {}
    for scan_path, prefix in _scan_paths(remote, folders):
        try:
            items = rclone.lsjson(scan_path, filters=filters, with_hash=True)
        except rclone.RcloneError as exc:
            result.errors.append(str(exc))
            continue
        for it in items:
            rel = it.get("Path", "")
            full = (prefix + "/" + rel) if prefix else rel
            # never touch files already inside the quarantine folder
            if full == quarantine or full.startswith(quarantine + "/"):
                continue
            result.files_scanned += 1
            it["_full"] = full
            picked = _best_hash(it.get("Hashes") or {})
            if not picked:
                result.skipped_no_hash.append(full)
                continue
            by_hash.setdefault(picked, []).append(it)

    for (htype, hval), files in by_hash.items():
        if len(files) < 2:
            continue
        keep, rest = _pick_canonical(files)
        result.groups.append(DupGroup(htype, hval, keep, rest))

    result.finished = utcnow_iso()
    result.duration_s = round(time.time() - start, 1)
    return result


def apply_quarantine(result: DedupeResult, dry_run: bool = False) -> None:
    """Move every quarantined file to <remote>:<quarantine>/<original path>."""
    result.apply = not dry_run
    for group in result.groups:
        for f in group.quarantined:
            full = f["_full"]
            src = _join(result.remote, full)
            dst = _join(result.remote, f"{result.quarantine}/{full}")
            ok, err = rclone.moveto(src, dst, dry_run=dry_run)
            f["_moved"] = ok and not dry_run
            if not ok:
                result.errors.append(f"{full}: {err}")
