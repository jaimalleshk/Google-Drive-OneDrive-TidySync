"""Command-line interface for tidysync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from tidysync import __version__, dedupe, gdocs, interactive, rclone, schedule
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
    try:
        url = Path(html_path).resolve().as_uri()
    except Exception:
        url = str(html_path)
    print(f"  report: {html_path}")
    print(f"  open:   {url}")


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


def _open_report(html_path: Path) -> None:
    try:
        import webbrowser
        webbrowser.open(Path(html_path).resolve().as_uri())
    except Exception:
        pass


def _do_run(cfg: AppConfig, pair_name: str, since: Optional[str],
            dry_run: Optional[bool], progress: bool = False,
            open_report: bool = False) -> int:
    store = _store(cfg)
    pair = cfg.pair(pair_name)
    result = run_pair(cfg, pair, store, since_override=since,
                      dry_run_override=dry_run, progress=progress)
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
    if open_report:
        _open_report(html_path)
    return 1 if result.errors else 0


def _progress_on(args) -> bool:
    """Show live progress when at a terminal and not silenced."""
    if getattr(args, "quiet", False):
        return False
    return interactive.is_tty()


def cmd_run(args) -> int:
    # Human at a terminal: show the config, confirm, fill anything missing.
    if interactive.enabled(args):
        if not interactive.review_and_complete(args.config, args.pair):
            print("Aborted — config not confirmed.")
            return 1
    cfg = _load(args)
    on = _progress_on(args)
    return _do_run(cfg, args.pair, args.since, _dry(args),
                   progress=on, open_report=on and not args.no_open)


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
            rc |= _do_run(cfg, name, args.since, _dry(args), progress=_progress_on(args))
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


def _print_dedupe(args, result, html_path) -> None:
    t = result.totals
    mode = "APPLIED" if result.apply else "REPORT-ONLY"
    print(f"\n{mode} {args.remote}: scanned={t['files_scanned']} "
          f"dup_groups={t['duplicate_groups']} duplicates={t['duplicates']} "
          f"reclaimable_bytes={t['reclaimable_bytes']} "
          f"no_hash={t['skipped_no_hash']} small/empty={t['skipped_small']} "
          f"errors={t['errors']}")
    if result.errors:
        for e in result.errors[:20]:
            print(f"    x {e}")
    try:
        url = Path(html_path).resolve().as_uri()
    except Exception:
        url = str(html_path)
    print(f"  report: {html_path}")
    print(f"  open:   {url}")


def cmd_dedupe(args) -> int:
    cfg = _load(args)
    rclone.ensure_rclone()
    if args.remote not in cfg.remotes:
        known = ", ".join(cfg.remotes) or "(none)"
        print(f"error: unknown remote '{args.remote}'. Configured remotes: {known}",
              file=sys.stderr)
        return 2
    remote = cfg.remotes[args.remote]
    prog = _progress_on(args)
    result = dedupe.find_duplicates(
        remote, folders=args.folder, quarantine=args.quarantine, progress=prog,
        min_size=getattr(args, "min_size", 1))

    want_apply = bool(args.apply)
    # Mandate report-first: in interactive use (menu), always show the report, then ask
    # before quarantining. CLI default stays report-only; --apply is the explicit override.
    if (not want_apply and getattr(args, "confirm", False)
            and result.groups and interactive.is_tty()):
        html_path, _, _ = write_dedupe_report(result, cfg.reports_dir)
        _print_dedupe(args, result, html_path)
        _open_report(html_path)
        t = result.totals
        want_apply = interactive.ask_yes_no(
            f"\nReview the report above. Move {t['duplicates']} duplicate(s) to "
            f"'{args.quarantine}/' (for review; not deleted) now?", default=False)
        if not want_apply:
            print("  Kept as report-only — nothing was moved.")
            return 1 if result.errors else 0

    if want_apply and result.groups:
        if prog:
            t = result.totals
            print(f"  found {t['duplicates']} duplicate file(s) in {t['duplicate_groups']} "
                  f"group(s); moving to '{args.quarantine}/' ...", file=sys.stderr)
        dedupe.apply_quarantine(result, dry_run=False, progress=prog)

    html_path, _, _ = write_dedupe_report(result, cfg.reports_dir)
    _print_dedupe(args, result, html_path)
    if result.groups and not result.apply:
        print("  (report-only by default — re-run with --apply, or use the menu, to quarantine)")
    return 1 if result.errors else 0


def cmd_convert(args) -> int:
    cfg = _load(args)
    rclone.ensure_rclone()
    if args.remote not in cfg.remotes:
        known = ", ".join(cfg.remotes) or "(none)"
        print(f"error: unknown remote '{args.remote}'. Configured remotes: {known}",
              file=sys.stderr)
        return 2
    remote = cfg.remotes[args.remote]
    rtype = rclone.remote_type(remote)
    if rtype != "drive":
        print(f"error: '{args.remote}' is type '{rtype or 'unknown'}'. "
              "Google-doc conversion applies only to Google Drive remotes.", file=sys.stderr)
        return 2
    res = gdocs.run_convert(remote, folders=args.folder, dry_run=not args.apply,
                            progress=_progress_on(args),
                            refresh=getattr(args, "refresh", False))
    t = res.totals
    mode = "CONVERTED" if res.apply else "REPORT-ONLY"
    print(f"\n{mode} {args.remote}: converted={t['converted']} "
          f"already_exist={t['uptodate']} "
          f"unsupported={t['unsupported']} errors={t['errors']}")
    for c in res.converted[:30]:
        print(f"    {c['path']} -> {c['out']}")
    if not res.apply and res.converted:
        print("  (re-run with --apply to create these Office files on Google Drive)")
    for e in res.errors[:20]:
        print(f"    x {e}")
    return 1 if res.errors else 0


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
    sp.add_argument("--quiet", action="store_true", help="No live progress bar / trail.")
    sp.add_argument("--no-open", action="store_true",
                    help="Don't auto-open the HTML report when finished.")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("run-all", help="Run every configured pair.")
    sp.add_argument("--since", help="Override delta start for all pairs.")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--quiet", action="store_true", help="No live progress bar / trail.")
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
    sp.add_argument("--min-size", type=int, default=1,
                    help="Ignore files smaller than this many bytes (default 1: skips empty files).")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser(
        "convert",
        help="Export Google Workspace docs to Office files on a Google Drive remote.")
    sp.add_argument("remote", help="A Google Drive remote key from config 'remotes'.")
    sp.add_argument("--folder", action="append",
                    help="Limit to this folder (repeatable). Default: the whole remote.")
    sp.add_argument("--apply", action="store_true",
                    help="Create the Office files on Drive (default: report only).")
    sp.add_argument("--refresh", action="store_true",
                    help="Also re-convert docs changed since their existing Office copy.")
    sp.set_defaults(func=cmd_convert)

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
