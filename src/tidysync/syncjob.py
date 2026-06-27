"""Orchestrate a single sync pair: resolve delta window, enumerate, copy, collect."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tidysync import gdocs, rclone
from tidysync.config import AppConfig, PairConfig
from tidysync.dedupe import QUARANTINE_DIR
from tidysync.state import StateStore, utcnow_iso


class SyncError(Exception):
    pass


def _trail(msg: str, on: bool) -> None:
    if on:
        print(msg, file=sys.stderr, flush=True)


@dataclass
class RunResult:
    pair: str
    mode: str
    scope: str
    since_spec: str          # what the user/config asked for (e.g. "last-sync")
    window: str              # actual value passed to rclone --max-age
    dry_run: bool
    started: str
    finished: str = ""
    duration_s: float = 0.0
    items: List[dict] = field(default_factory=list)   # one row per delta file
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    converted: List[dict] = field(default_factory=list)  # Google docs -> Office on Drive
    conversion_uptodate: int = 0

    @property
    def totals(self) -> Dict[str, int]:
        t = {"created": 0, "updated": 0, "skipped_identical": 0,
             "conflicts": len(self.conflicts), "errors": len(self.errors),
             "bytes": 0}
        for it in self.items:
            if it["action"] == "created":
                t["created"] += 1
            elif it["action"] == "updated":
                t["updated"] += 1
            elif it["action"] == "skipped-identical":
                t["skipped_identical"] += 1
            if it["action"] in ("created", "updated"):
                t["bytes"] += int(it.get("size") or 0)
        return t


def _rpath(remote: str, folder: str) -> str:
    if not folder:
        return remote
    if remote.endswith(":"):
        return remote + folder
    return remote.rstrip("/") + "/" + folder


def _directions(pair: PairConfig) -> List[Tuple[str, str, str]]:
    """Return (label, src_remote, dst_remote) for each direction to run."""
    L, R = pair.left_remote, pair.right_remote
    if pair.mode == "left-to-right":
        return [("L->R", L, R)]
    if pair.mode == "right-to-left":
        return [("R->L", R, L)]
    return [("L->R", L, R), ("R->L", R, L)]


def _folders(pair: PairConfig) -> List[str]:
    return pair.folders if pair.scope == "folders" else [""]


def resolve_window(pair: PairConfig, store: StateStore,
                   since_override: Optional[str]) -> Tuple[str, str]:
    """Return (since_spec, max_age_value). Raises if no window can be determined."""
    spec = since_override or pair.since
    if spec == "last-sync":
        last = store.get_last_sync(pair.name)
        if not last:
            raise SyncError(
                f"Pair '{pair.name}' has no recorded last-sync yet. "
                "Provide a starting point explicitly, e.g.:\n"
                f"    tidysync run {pair.name} --since 2026-06-01\n"
                "(This guard prevents an accidental full-drive copy.)"
            )
        return ("last-sync", last)
    # A literal date ('2026-06-01') or duration ('720h') is passed straight to rclone.
    return (spec, spec)


def run_pair(cfg: AppConfig, pair: PairConfig, store: StateStore,
             since_override: Optional[str] = None,
             dry_run_override: Optional[bool] = None,
             progress: bool = False) -> RunResult:
    rclone.ensure_rclone()
    dry_run = pair.dry_run if dry_run_override is None else dry_run_override
    since_spec, window = resolve_window(pair, store, since_override)

    start = time.time()
    result = RunResult(
        pair=pair.name, mode=pair.mode, scope=pair.scope,
        since_spec=since_spec, window=window, dry_run=dry_run,
        started=utcnow_iso(),
    )

    directions = _directions(pair)
    folders = _folders(pair)
    # Never sync the dedupe quarantine folder between clouds.
    eff_filters = [f"- {QUARANTINE_DIR}/**"] + pair.filters

    tag = "[DRY RUN] " if dry_run else ""
    _trail(f"\n{tag}Sync '{pair.name}'  ({pair.mode}, {pair.scope}, since {window})",
           progress)

    # Pre-step: on any Google Drive source, export native Google docs to Office
    # files (.docx/.xlsx/.pptx) in place, so the sync copies usable files. The new
    # files land with a fresh modtime and are picked up by the delta window below.
    if pair.convert_google_docs:
        conv_folders = pair.folders if pair.scope == "folders" else None
        for src_remote in {sr for _, sr, _ in directions}:
            try:
                if rclone.remote_type(src_remote) != "drive":
                    continue
            except Exception as exc:  # detection failure shouldn't abort the sync
                result.errors.append(f"google-doc detection skipped: {exc}")
                continue
            _trail(f"  {'Would convert' if dry_run else 'Converting'} "
                   f"Google docs on {src_remote} ...", progress)
            cres = gdocs.run_convert(src_remote, conv_folders, eff_filters,
                                     dry_run=dry_run, progress=progress, refresh=True)
            result.converted.extend(cres.converted)
            result.conversion_uptodate += len(cres.skipped_uptodate)
            result.errors.extend(cres.errors)

    # Track per-folder deltas seen from each remote, to detect both-sides edits.
    seen: Dict[str, Dict[str, set]] = {}  # folder -> {"L": set(paths), "R": set(paths)}

    for label, src_remote, dst_remote in directions:
        side = "L" if label == "L->R" else "R"
        for folder in folders:
            src = _rpath(src_remote, folder)
            dst = _rpath(dst_remote, folder)

            slow = " (this can take a few minutes)" if pair.scope == "whole-drive" else ""
            _trail(f"\n[{label}] Scanning {src} for changes since {window}{slow} ...", progress)
            try:
                candidates = rclone.lsjson(
                    src, window, eff_filters,
                    spinner_label=(f"scanning {src}" if progress else None),
                    extra=pair.rclone_args)
            except rclone.RcloneError as exc:
                result.errors.append(str(exc))
                _trail(f"  ! scan failed: {exc}", progress)
                continue

            verb = "would copy" if dry_run else "copying"
            _trail(f"  {len(candidates)} changed/new file(s); {verb} {src} -> {dst} ...",
                   progress)

            seen.setdefault(folder, {"L": {}, "R": {}})
            for c in candidates:
                seen[folder][side][c["Path"]] = (c.get("Size"), c.get("ModTime", ""))

            copy_res = rclone.copy(src, dst, window, eff_filters,
                                   dry_run=dry_run, progress=progress,
                                   extra=pair.rclone_args)
            if dry_run:
                _trail("  (dry run: the figures above are SIMULATED - nothing was transferred)",
                       progress)
            result.errors.extend(copy_res.errors)
            created = set(copy_res.created)
            updated = set(copy_res.updated)

            for c in candidates:
                path = c["Path"]
                if path in created:
                    action = "created"
                elif path in updated:
                    action = "updated"
                else:
                    action = "skipped-identical"
                result.items.append({
                    "path": (folder + "/" + path) if folder else path,
                    "direction": label,
                    "action": action,
                    "size": c.get("Size"),
                    "modtime": c.get("ModTime", ""),
                })

    # Conflicts (two-way only): a file present in BOTH sides' windows whose SIZE
    # differs -- a strong signal the content genuinely diverged. Same-size files
    # (even with different modtimes) are treated as quiet newest-wins updates, not
    # conflicts, since cross-cloud we can't compare content and a size match almost
    # always means the same bytes (just clock drift). The latest is always taken.
    if pair.mode == "two-way":
        for folder, sides in seen.items():
            left, right = sides["L"], sides["R"]
            for path in sorted(set(left) & set(right)):
                (ls, _lm), (rs, _rm) = left[path], right[path]
                if ls == rs:
                    continue   # same size -> quiet newest-wins, not a conflict
                full = (folder + "/" + path) if folder else path
                result.conflicts.append(full)

    result.finished = utcnow_iso()
    result.duration_s = round(time.time() - start, 1)
    return result
