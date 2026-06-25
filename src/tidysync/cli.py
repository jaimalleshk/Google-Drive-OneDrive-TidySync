"""Command-line interface for tidysync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from tidysync import __version__, dedupe, interactive, rclone, schedule
from tidysync.config import AppConfig, ConfigError, default_config_path, load_config
from tidysync.report import write_dedupe_report, write_reports
from tidysync.state import StateStore
from tidysync.syncjob import RunResult, SyncError, run_pair

CONFIG_TEMPLATE = """\
# tidysync configuration
#
# 1. Install rclone and run `rclone config` to add your two remotes, e.g. one
#    named "gdrive" (Google Drive) and one named "onedrive" (OneDrive).
# 2. Map those rclone remote names below, then define one or more sync pairs.

remotes:
  gdrive: "gdrive:"
  onedrive: "onedrive:"

pairs:
  - name: projects
    left: gdrive            # a key from 'remotes'
    right: onedrive
    mode: two-way           # left-to-right | right-to-left | two-way
    scope: folders          # whole-drive | folders
    folders:                # used only when scope = folders (path on each remote)
      - "Projects/Active"
    delta:
      since: last-sync      # last-sync | a date "2026-06-01" | a duration "720h"
    filters:                # optional rclone filter rules
      - "- *.tmp"
      - "- ~$*"
    dry_run: false
"""


def _load(args) -> AppConfig:
    return load_config(args.config)


def _store(cfg: AppConfig) -> StateStore:
    return StateStore(cfg.state_dir)


def _print_result(result: RunResult, html_path: Path) -> None:
    t = result.totals
    head = "DRY RUN " if result.dry_run else ""
    print(f"\n{head}{result.pair}: created={t['created']} updated={t['updated']} "
          f"skipped={t['skipped_identical']} conflicts={t['conflicts']} "
          f"errors={t['errors']} bytes={t['bytes']}")
    if result.conflicts:
        print("  conflicts (newest-wins applied):")
        for c in result.conflicts[:20]:
            print(f"    ! {c}")
    if result.errors:
        print("  errors:")
        for e in result.errors[:20]:
            print(f"    x {e}")
    print(f"  report: {html_path}")


# --- subcommands ---------------------------------------------------------

def cmd_init(args) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"Config already exists: {path} (use --force to overwrite)")
    else:
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(f"Wrote config template: {path}")
    (path.parent / "state").mkdir(exist_ok=True)
    (path.parent / "reports").mkdir(exist_ok=True)

    exe = rclone.find_rclone()
    if exe:
        print(f"rclone found: {rclone.version()}")
        remotes = rclone.list_remotes()
        print(f"Configured rclone remotes: {', '.join(remotes) if remotes else '(none yet)'}")
    else:
        print("\nrclone is NOT installed. Install it, then add your remotes:")
        print("  winget install Rclone.Rclone")
        print("  rclone config        # add a Google Drive remote and a OneDrive remote")
    print("\nNext: edit the config, then run `tidysync check`.")
    return 0


def cmd_check(args) -> int:
    cfg = _load(args)
    rclone.ensure_rclone()
    print(f"rclone: {rclone.version()}")
    available = set(rclone.list_remotes())
    ok = True
    checked = set()
    for pair in cfg.pairs.values():
        for remote in (pair.left_remote, pair.right_remote):
            if remote in checked:
                continue
            checked.add(remote)
            name = remote.split(":")[0] + ":"
            if name not in available:
                print(f"  ! {remote}: remote '{name}' not found in rclone config")
                ok = False
                continue
            try:
                rclone.check_remote(remote)
                print(f"  ok {remote}")
            except rclone.RcloneError as exc:
                print(f"  ! {remote}: {exc}")
                ok = False
    print("All remotes reachable." if ok else "Some remotes failed — see above.")
    return 0 if ok else 1


def _do_run(cfg: AppConfig, pair_name: str, since: Optional[str],
            dry_run: Optional[bool]) -> int:
    store = _store(cfg)
    pair = cfg.pair(pair_name)
    result = run_pair(cfg, pair, store, since_override=since, dry_run_override=dry_run)
    html_path, _, _ = write_reports(result, cfg.reports_dir)
    # Persist last-sync only on a real, successful run.
    if not result.dry_run and not result.errors:
        store.set_last_sync(pair.name, result.started, report=str(html_path))
    else:
        # still remember where the latest report is
        last = store.get_last_sync(pair.name)
        if last:
            store.set_last_sync(pair.name, last, report=str(html_path))
    _print_result(result, html_path)
    return 1 if result.errors else 0


def cmd_run(args) -> int:
    # Human at a terminal: show the config, confirm, fill anything missing.
    if interactive.enabled(args):
        if not interactive.review_and_complete(args.config, args.pair):
            print("Aborted — config not confirmed.")
            return 1
    cfg = _load(args)
    return _do_run(cfg, args.pair, args.since, _dry(args))


def cmd_menu(args) -> int:
    return interactive.menu(args.config)


def cmd_configure(args) -> int:
    interactive.configure_pair(args.config)
    return 0


def cmd_run_all(args) -> int:
    cfg = _load(args)
    rc = 0
    for name in cfg.pairs:
        try:
            rc |= _do_run(cfg, name, args.since, _dry(args))
        except SyncError as exc:
            print(f"{name}: skipped — {exc}")
            rc = 1
    return rc


def cmd_status(args) -> int:
    cfg = _load(args)
    store = _store(cfg)
    print(f"{'PAIR':<16}{'MODE':<14}{'SCOPE':<12}{'LAST SYNC (UTC)':<22}LAST REPORT")
    for pair in cfg.pairs.values():
        last = store.get_last_sync(pair.name) or "(never)"
        report = store.get_last_report(pair.name) or ""
        print(f"{pair.name:<16}{pair.mode:<14}{pair.scope:<12}{last:<22}{report}")
    return 0


def cmd_schedule(args) -> int:
    cfg = _load(args)
    cfg.pair(args.pair)  # validate name
    try:
        tn = schedule.create(args.pair, Path(args.config).resolve(),
                             every=args.every, daily=args.daily)
    except schedule.ScheduleError as exc:
        print(f"Failed to schedule: {exc}", file=sys.stderr)
        return 1
    print(f"Created scheduled task '{tn}'.")
    print(schedule.query(args.pair))
    return 0


def cmd_unschedule(args) -> int:
    try:
        tn = schedule.delete(args.pair)
    except schedule.ScheduleError as exc:
        print(f"Failed to remove task: {exc}", file=sys.stderr)
        return 1
    print(f"Removed scheduled task '{tn}'.")
    return 0


def cmd_dedupe(args) -> int:
    cfg = _load(args)
    rclone.ensure_rclone()
    if args.remote not in cfg.remotes:
        known = ", ".join(cfg.remotes) or "(none)"
        print(f"error: unknown remote '{args.remote}'. Configured remotes: {known}",
              file=sys.stderr)
        return 2
    remote = cfg.remotes[args.remote]
    result = dedupe.find_duplicates(
        remote, folders=args.folder, quarantine=args.quarantine)
    if args.apply and result.groups:
        dedupe.apply_quarantine(result, dry_run=False)

    html_path, _, _ = write_dedupe_report(result, cfg.reports_dir)
    t = result.totals
    mode = "APPLIED" if result.apply else "REPORT-ONLY"
    print(f"\n{mode} {args.remote}: scanned={t['files_scanned']} "
          f"dup_groups={t['duplicate_groups']} duplicates={t['duplicates']} "
          f"reclaimable_bytes={t['reclaimable_bytes']} "
          f"no_hash={t['skipped_no_hash']} errors={t['errors']}")
    if result.groups and not args.apply:
        print(f"  (re-run with --apply to move {t['duplicates']} duplicate(s) "
              f"to '{args.quarantine}/' for review)")
    if result.errors:
        for e in result.errors[:20]:
            print(f"    x {e}")
    print(f"  report: {html_path}")
    return 1 if result.errors else 0


def _dry(args) -> Optional[bool]:
    return True if getattr(args, "dry_run", False) else None


# --- parser --------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tidysync",
        description="Delta sync between Google Drive and OneDrive (built on rclone).",
    )
    p.add_argument("--version", action="version", version=f"tidysync {__version__}")
    p.add_argument("--config", default=str(default_config_path()),
                   help="Path to config.yaml (default: ./config.yaml or $TIDYSYNC_CONFIG)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Non-interactive: skip confirmation prompts (used by the scheduler).")
    sub = p.add_subparsers(dest="command", required=False)

    sp = sub.add_parser("menu", help="Open the interactive menu (default with no command).")
    sp.set_defaults(func=cmd_menu)

    sp = sub.add_parser("configure", help="Add or edit a sync pair interactively.")
    sp.set_defaults(func=cmd_configure)

    sp = sub.add_parser("init", help="Create a config template and check rclone.")
    sp.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("check", help="Validate config and verify remotes are reachable.")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("run", help="Run one sync pair now.")
    sp.add_argument("pair")
    sp.add_argument("--since", help="Override delta start: date '2026-06-01' or duration '720h'.")
    sp.add_argument("--dry-run", action="store_true", help="Report only; transfer nothing.")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("run-all", help="Run every configured pair.")
    sp.add_argument("--since", help="Override delta start for all pairs.")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_run_all)

    sp = sub.add_parser("status", help="Show last-sync time and latest report per pair.")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser(
        "dedupe",
        help="Find content-duplicate files within ONE cloud; quarantine extras for review.")
    sp.add_argument("remote", help="A remote key from config 'remotes' (e.g. gdrive).")
    sp.add_argument("--folder", action="append",
                    help="Limit to this folder (repeatable). Default: the whole remote.")
    sp.add_argument("--apply", action="store_true",
                    help="Move duplicates to the quarantine folder (default: report only).")
    sp.add_argument("--quarantine", default=dedupe.QUARANTINE_DIR,
                    help=f"Quarantine folder name (default: {dedupe.QUARANTINE_DIR}).")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser("schedule", help="Create a Windows scheduled task for a pair.")
    sp.add_argument("pair")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--every", help="Interval, e.g. '30m' or '2h'.")
    g.add_argument("--daily", help="Daily time, e.g. '02:00'.")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("unschedule", help="Remove a pair's scheduled task.")
    sp.add_argument("pair")
    sp.set_defaults(func=cmd_unschedule)

    return p


def main(argv=None) -> int:
    # Make console output robust to non-ASCII paths on Windows (cp1252) consoles.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # No subcommand: open the menu in a terminal, else show help.
        if interactive.is_tty():
            return interactive.menu(args.config)
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (ConfigError, SyncError, rclone.RcloneError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
