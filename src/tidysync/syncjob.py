"""Orchestrate a single sync pair: resolve delta window, enumerate, copy, collect."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tidysync import rclone
from tidysync.config import AppConfig, PairConfig
from tidysync.dedupe import QUARANTINE_DIR
from tidysync.state import StateStore, utcnow_iso


class SyncError(Exception):
    pass


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
             dry_run_override: Optional[bool] = None) -> RunResult:
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

    # Track per-folder deltas seen from each remote, to detect both-sides edits.
    seen: Dict[str, Dict[str, set]] = {}  # folder -> {"L": set(paths), "R": set(paths)}

    for label, src_remote, dst_remote in directions:
        side = "L" if label == "L->R" else "R"
        for folder in folders:
            src = _rpath(src_remote, folder)
            dst = _rpath(dst_remote, folder)

            try:
                candidates = rclone.lsjson(src, window, eff_filters)
            except rclone.RcloneError as exc:
                result.errors.append(str(exc))
                continue

            seen.setdefault(folder, {"L": set(), "R": set()})
            for c in candidates:
                seen[folder][side].add(c["Path"])

            copy_res = rclone.copy(src, dst, window, eff_filters, dry_run=dry_run)
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

    # Conflicts: file changed on BOTH sides within the window (two-way only).
    if pair.mode == "two-way":
        for folder, sides in seen.items():
            both = sides["L"] & sides["R"]
            for path in sorted(both):
                full = (folder + "/" + path) if folder else path
                result.conflicts.append(full)

    result.finished = utcnow_iso()
    result.duration_s = round(time.time() - start, 1)
    return result
