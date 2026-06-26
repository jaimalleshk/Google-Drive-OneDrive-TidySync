"""Per-cloud, content-hash duplicate detection with quarantine-for-review.

Duplicates are decided purely by content hash (NOT filename) within a SINGLE
remote — comparing hashes across clouds is meaningless (different algorithms).
The newest-modified copy in each group is kept; older copies are moved to a
quarantine folder for the user to review and delete. Files only; never folders.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tidysync import rclone
from tidysync.state import utcnow_iso


def norm_ext(ext: str) -> str:
    """Normalise a file extension to '.ext' lowercase."""
    ext = (ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return ext

QUARANTINE_DIR = "_duplicates"

# Prefer strong, widely-available hashes first.
HASH_PREFERENCE = ["sha256", "sha1", "md5", "quickxor", "crc32", "blake3", "whirlpool"]

# Build / dependency / cache directories skipped by default during dedupe — these are
# byte-identical across projects but are junk you must NOT quarantine (it would break
# virtualenvs and builds). rclone filter rules; disable with --no-default-excludes.
DEFAULT_EXCLUDES = [
    "- **/.git/**", "- **/.svn/**", "- **/.hg/**",
    "- **/node_modules/**", "- **/bower_components/**",
    "- **/.venv/**", "- **/venv/**", "- **/env/**", "- **/__pycache__/**",
    "- **/.mypy_cache/**", "- **/.pytest_cache/**", "- **/.tox/**",
    "- **/*.egg-info/**", "- **/*.dist-info/**", "- **/site-packages/**",
    "- **/obj/**", "- **/bin/**", "- **/build/**", "- **/dist/**", "- **/out/**",
    "- **/target/**", "- **/.gradle/**", "- **/Pods/**", "- **/Carthage/**",
    "- **/.next/**", "- **/.nuxt/**", "- **/.cache/**",
    "- **/.idea/**", "- **/.vs/**", "- **/.vscode/**",
    # OneDrive Personal Vault is a locked area the API cannot enumerate
    # (errors with 'ObjectHandle is Invalid'); always skip it.
    "- Personal Vault/**", "- **/Personal Vault/**",
]


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
    skipped_small: List[str] = field(default_factory=list)
    skipped_type: List[str] = field(default_factory=list)
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
            "skipped_small": len(self.skipped_small),
            "skipped_type": len(self.skipped_type),
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


def _scan_targets(remote, folders, filters, extra, quarantine, result):
    """Return (scan_path, prefix, max_depth) targets to scan.

    For an explicit folder list, scan each. For whole-drive, scan each TOP-LEVEL
    folder separately (plus root-level files) so one unlistable folder — e.g.
    OneDrive Personal Vault — only fails itself instead of aborting everything.
    """
    if folders:
        return [(_join(remote, f), f, None) for f in folders]
    try:
        topdirs = rclone.list_dirs(remote, filters=filters, extra=extra)
    except rclone.RcloneError as exc:
        result.errors.append(f"could not list top-level folders: {exc}")
        return [(remote, "", None)]   # fallback: single recursive scan
    topdirs = [d for d in topdirs if d != quarantine]
    targets = [(remote, "", 1)]       # root-level loose files (depth 1)
    targets += [(_join(remote, d), d, None) for d in topdirs]
    return targets


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
                    quarantine: str = QUARANTINE_DIR,
                    progress: bool = False, min_size: int = 1,
                    only_types: Optional[List[str]] = None,
                    skip_types: Optional[List[str]] = None,
                    extra: Optional[List[str]] = None) -> DedupeResult:
    """Find content-duplicate files on one remote.

    only_types: if set, ONLY consider files with these extensions (the "only" list).
    skip_types: additionally skip files with these extensions.
    """
    rclone.ensure_rclone()
    start = time.time()
    only = {norm_ext(e) for e in only_types} if only_types else None
    skip = {norm_ext(e) for e in skip_types} if skip_types else set()
    result = DedupeResult(
        remote=remote,
        scope_desc=("folders: " + ", ".join(folders)) if folders else "whole drive",
        quarantine=quarantine, apply=False, started=utcnow_iso(),
    )

    by_hash: Dict[Tuple[str, str], List[dict]] = {}
    targets = _scan_targets(remote, folders, filters, extra, quarantine, result)
    for scan_path, prefix, max_depth in targets:
        # --fast-list ignores --max-depth (lists everything first), so drop it for
        # the shallow root scan to avoid re-traversing unlistable folders.
        eff_extra = ([a for a in (extra or []) if a != "--fast-list"]
                     if max_depth is not None else extra)
        try:
            items = rclone.lsjson(
                scan_path, filters=filters, with_hash=True,
                spinner_label=(f"hashing {scan_path}" if progress else None),
                extra=eff_extra, max_depth=max_depth)
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
            # File-type filter: "only" list (whitelist) and skip list (blacklist).
            ext = os.path.splitext(full)[1].lower()
            if (only is not None and ext not in only) or (ext in skip):
                result.skipped_type.append(full)
                continue
            size = it.get("Size")
            # Skip empty / tiny files: all empty files share one hash, which would
            # otherwise group thousands of unrelated files as bogus "duplicates".
            if size is None or size < min_size:
                result.skipped_small.append(full)
                continue
            picked = _best_hash(it.get("Hashes") or {})
            if not picked:
                result.skipped_no_hash.append(full)
                continue
            # Group by hash AND size (same content => same hash & size).
            by_hash.setdefault((picked[0], picked[1], size), []).append(it)

    for (htype, hval, _size), files in by_hash.items():
        if len(files) < 2:
            continue
        keep, rest = _pick_canonical(files)
        result.groups.append(DupGroup(htype, hval, keep, rest))

    result.finished = utcnow_iso()
    result.duration_s = round(time.time() - start, 1)
    return result


def apply_quarantine(result: DedupeResult, dry_run: bool = False,
                     progress: bool = False, extra: Optional[List[str]] = None) -> None:
    """Move all quarantined files into <remote>:<quarantine>/ in ONE server-side
    rclone move (fast), preserving each file's original relative path."""
    result.apply = not dry_run
    paths = [f["_full"] for g in result.groups for f in g.quarantined]
    if not paths:
        return
    if progress:
        import sys
        sys.stderr.write(f"  moving {len(paths)} file(s) to {result.quarantine}/ "
                         "(single server-side move)...\n")
        sys.stderr.flush()
    ok, errs = rclone.move_batch(result.remote, result.quarantine, paths,
                                 extra=extra, dry_run=dry_run, progress=progress)
    result.errors.extend(errs)
    moved = ok and not dry_run
    for g in result.groups:
        for f in g.quarantined:
            f["_moved"] = moved
