"""Interactive menu + config confirm/complete wizard.

Running `tidysync` with no arguments (in a real terminal) launches the menu.
When a human runs a sync, the tool shows the pair's config, lets them confirm,
and prompts for anything missing — then writes it back to config.yaml. Under the
scheduler (no TTY, or `--yes`) everything runs non-interactively.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

from tidysync import dedupe, rclone
from tidysync.config import (VALID_MODES, VALID_SCOPES, load_config, load_raw,
                             save_raw)


# --- environment detection ----------------------------------------------

def is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def enabled(args) -> bool:
    """Interactive confirmation should run for this invocation?"""
    return is_tty() and not getattr(args, "yes", False)


# --- prompt helpers ------------------------------------------------------

def ask(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        val = input(f"{text}{suffix}: ").strip()
    except EOFError:
        return default or ""
    return val or (default or "")


def ask_yes_no(text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            v = input(f"{text} [{hint}]: ").strip().lower()
        except EOFError:
            return default
        if not v:
            return default
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False


def ask_choice(text: str, options: List[str], default: Optional[str] = None) -> str:
    print(text)
    for i, o in enumerate(options, 1):
        mark = "  (default)" if o == default else ""
        print(f"  {i}) {o}{mark}")
    while True:
        try:
            v = input("Choose number: ").strip()
        except EOFError:
            return default or options[0]
        if not v and default:
            return default
        if v.isdigit() and 1 <= int(v) <= len(options):
            return options[int(v) - 1]
        print("  ! enter a number from the list.")


def ask_list(text: str, default: Optional[List[str]] = None) -> List[str]:
    default = default or []
    shown = ", ".join(default)
    print(f"{text}")
    print("  (comma-separated; press Enter to keep current)")
    raw = ask("  folders", default=shown)
    return [f.strip().strip("/") for f in raw.split(",") if f.strip()]


def available_remotes() -> List[str]:
    try:
        return rclone.list_remotes()
    except rclone.RcloneError:
        return []


def ask_remote(label: str, available: List[str], default: Optional[str] = None) -> str:
    if available:
        opts = available + ["<type another remote>"]
        choice = ask_choice(label, opts, default=default if default in available else None)
        if choice == "<type another remote>":
            choice = ask(f"{label} (rclone remote, e.g. gdrive:)", default=default)
    else:
        choice = ask(f"{label} (rclone remote, e.g. gdrive:)", default=default)
    choice = choice.strip()
    if choice and ":" not in choice:
        choice += ":"
    return choice


# --- config editing ------------------------------------------------------

def _missing_fields(pr: dict, remotes: dict) -> List[str]:
    missing = []
    for side in ("left", "right"):
        key = pr.get(side)
        if not key or key not in remotes or not remotes.get(key):
            missing.append(f"{side} remote")
    if pr.get("mode") not in VALID_MODES:
        missing.append("mode")
    scope = pr.get("scope")
    if scope not in VALID_SCOPES:
        missing.append("scope")
    if scope == "folders" and not pr.get("folders"):
        missing.append("folders")
    if not (pr.get("delta") or {}).get("since"):
        missing.append("delta.since")
    return missing


def flow_arrow(mode: str, lkey: str, rkey: str) -> str:
    """Human-readable sync direction for a pair."""
    if mode == "left-to-right":
        return f"{lkey} --> {rkey}"
    if mode == "right-to-left":
        return f"{rkey} --> {lkey}"
    return f"{lkey} <-> {rkey}"   # two-way


def _print_pair(pr: dict, remotes: dict) -> None:
    lkey, rkey = pr.get("left"), pr.get("right")
    mode = pr.get("mode")
    print("\n  ---------------------------------------------")
    print(f"  pair    : {pr.get('name')}")
    print(f"  sync    : {flow_arrow(mode, lkey, rkey)}   ({mode})")
    print(f"  clouds  : {lkey}={remotes.get(lkey, '?')}  {rkey}={remotes.get(rkey, '?')}")
    print(f"  scope   : {pr.get('scope')}")
    if pr.get("scope") == "folders":
        print(f"  folders : {', '.join(pr.get('folders') or []) or '(none)'}")
    print(f"  since   : {(pr.get('delta') or {}).get('since')}")
    print("  ---------------------------------------------")


def _edit_pair(pr: dict, remotes: dict, available: List[str]) -> None:
    for side in ("left", "right"):
        cur_key = pr.get(side)
        cur_remote = remotes.get(cur_key) if cur_key else None
        label = "First cloud (left)" if side == "left" else "Second cloud (right)"
        remote = ask_remote(label, available, default=cur_remote)
        if not remote:
            continue
        key = remote.rstrip(":") or side
        remotes[key] = remote
        pr[side] = key

    pr["mode"] = ask_choice("Sync direction (mode)", sorted(VALID_MODES),
                            default=pr.get("mode", "two-way"))
    pr["scope"] = ask_choice("Scope", sorted(VALID_SCOPES),
                             default=pr.get("scope", "folders"))
    if pr["scope"] == "folders":
        pr["folders"] = ask_list("Folders to sync (relative path on each cloud)",
                                 default=pr.get("folders"))
    else:
        pr.pop("folders", None)
    since = ask("Delta start (last-sync | a date like 2026-06-01 | a duration like 720h)",
                default=(pr.get("delta") or {}).get("since", "last-sync"))
    pr["delta"] = {"since": since}


def _find_pair(pairs: List[dict], name: str) -> Optional[dict]:
    for p in pairs:
        if p.get("name") == name:
            return p
    return None


def review_and_complete(config_path: Path, pair_name: str) -> bool:
    """Show/confirm/complete one pair before a run. Returns True to proceed."""
    raw = load_raw(config_path)
    before = deepcopy(raw)
    remotes = raw.setdefault("remotes", {})
    pairs = raw.setdefault("pairs", [])
    available = available_remotes()

    pr = _find_pair(pairs, pair_name)
    if pr is None:
        if not ask_yes_no(f"Pair '{pair_name}' is not in the config. Create it now?", True):
            return False
        pr = {"name": pair_name}
        pairs.append(pr)
        _edit_pair(pr, remotes, available)

    while True:
        _print_pair(pr, remotes)
        missing = _missing_fields(pr, remotes)
        if missing:
            print(f"  Missing/invalid: {', '.join(missing)} - let's fill these in.")
            _edit_pair(pr, remotes, available)
            continue
        if ask_yes_no("Proceed with this configuration?", True):
            break
        if ask_yes_no("Edit it?", True):
            _edit_pair(pr, remotes, available)
        else:
            return False

    if raw != before:
        save_raw(config_path, raw)
        print(f"  Saved config: {Path(config_path)}")
    return True


def configure_pair(config_path: Path) -> None:
    """Add or edit a pair from the menu (no run)."""
    raw = load_raw(config_path)
    before = deepcopy(raw)
    remotes = raw.setdefault("remotes", {})
    pairs = raw.setdefault("pairs", [])
    available = available_remotes()

    names = [p.get("name") for p in pairs]
    options = names + ["<new pair>"]
    choice = ask_choice("Configure which pair?", options,
                        default=(names[0] if names else "<new pair>"))
    if choice == "<new pair>":
        name = ask("New pair name")
        if not name:
            print("  cancelled.")
            return
        pr = _find_pair(pairs, name)
        if pr is None:
            pr = {"name": name}
            pairs.append(pr)
    else:
        pr = _find_pair(pairs, choice)

    _edit_pair(pr, remotes, available)
    _print_pair(pr, remotes)
    if raw != before:
        save_raw(config_path, raw)
        print(f"  Saved config: {Path(config_path)}")
    else:
        print("  No changes.")


# --- main menu -----------------------------------------------------------

MENU = """
================ TidySync : Google Drive <-> OneDrive ================
  SYNC
    1) Run a sync pair        copy recent changes between two clouds
    2) Run all pairs          run every configured pair
  MAINTAIN  (one cloud at a time)
    3) Find duplicates        by content; move extras to a review folder
    4) Convert Google docs    make .docx/.xlsx/.pptx copies on Google Drive
  SETUP
    5) Configure a pair       clouds, direction, folders, time window
    6) Schedule a pair        7) Unschedule a pair
    8) Status                 9) Check remotes
   10) Create / repair config
  ---------------------------------------------------------------------
    h) Help (what each option does)                 0) Quit
=====================================================================
"""

HELP = """
TidySync keeps Google Drive and OneDrive in sync and tidy.

 1) Run a sync pair       Copy files changed within your time window between the
                          two clouds (one-way or two-way). Never deletes; the
                          most-recently-modified copy wins. A dry-run option lets
                          you preview first.
 2) Run all pairs         Do option 1 for every pair in your config.

 3) Find duplicates       Within ONE cloud, find files with identical CONTENT
                          (any name, any folder), keep the newest, and move the
                          rest to a _duplicates/ folder for you to review/delete.
                          Nothing is ever deleted automatically.
 4) Convert Google docs   On Google Drive, export native Google Docs/Sheets/Slides
                          to real .docx/.xlsx/.pptx files in the SAME folder,
                          recursively. By default it only creates copies that don't
                          already exist (optionally refresh ones whose doc changed).
                          Native Google files can't sync to OneDrive usefully alone.

 5) Configure a pair      Add or edit a pair: the two clouds, direction
                          (one-way / two-way), scope (whole drive or folders),
                          and the delta time window.
 6/7) Schedule / Unschedule  Run a pair automatically (every N minutes or daily),
                          or remove that schedule.
 8) Status                Last-sync time and latest report for each pair.
 9) Check remotes         Verify both clouds are reachable.
10) Create / repair config  Write a fresh config template.

Safety: dry-run previews everywhere, sync never deletes, dedupe/convert never
auto-delete. Reports (HTML/CSV/JSON) are written to the reports/ folder.
"""


def _ns(config_path, **kw):
    return SimpleNamespace(config=str(config_path), yes=True, **kw)


def _pick_pair_name(config_path) -> Optional[str]:
    raw = load_raw(config_path)
    names = [p.get("name") for p in (raw.get("pairs") or [])]
    if not names:
        print("  No pairs configured yet - use option 5 to add one.")
        return None
    return ask_choice("Which pair?", names, default=names[0])


def _pick_remote_key(config_path) -> Optional[str]:
    raw = load_raw(config_path)
    keys = list((raw.get("remotes") or {}).keys())
    if not keys:
        print("  No remotes configured - use option 5 or edit the config.")
        return None
    return ask_choice("Which cloud?", keys, default=keys[0])


def menu(config_path: Path) -> int:
    from tidysync import cli  # lazy import to avoid a circular import at load time

    while True:
        print(MENU)
        choice = ask("Select")
        try:
            if choice == "1":
                name = _pick_pair_name(config_path)
                if not name:
                    continue
                if not review_and_complete(config_path, name):
                    print("  aborted.")
                    continue
                dry = ask_yes_no("Dry run (report only, no transfer)?", default=False)
                cli._do_run(load_config(config_path), name, since=None,
                            dry_run=True if dry else None, progress=True,
                            open_report=True)
            elif choice == "2":
                dry = ask_yes_no("Dry run for all pairs?", default=False)
                cli.cmd_run_all(_ns(config_path, since=None, dry_run=dry))
            elif choice == "3":
                key = _pick_remote_key(config_path)
                if not key:
                    continue
                folder = ask("  Limit to a folder? (blank = whole cloud; "
                             "a folder is faster and avoids API rate limits)", default="")
                folders = [folder.strip().strip("/")] if folder.strip() else None
                print("  Running a REPORT first (nothing is moved). "
                      "Review it, then you'll be asked whether to quarantine.")
                cli.cmd_dedupe(_ns(config_path, remote=key, folder=folders,
                                   apply=False, quarantine=dedupe.QUARANTINE_DIR,
                                   min_size=1, confirm=True))
            elif choice == "4":
                print("  Export native Google docs (Docs/Sheets/Slides) to Office files on")
                print("  Google Drive, recursively, in the same folder. Only creates copies")
                print("  that don't already exist.")
                key = _pick_remote_key(config_path)
                if not key:
                    continue
                folder = ask("  Limit to a folder? (blank = whole drive)", default="")
                folders = [folder.strip().strip("/")] if folder.strip() else None
                refresh = ask_yes_no("  Also re-convert docs changed since their Office copy?",
                                     default=False)
                apply = ask_yes_no("  Create the Office files now? (No = report only)",
                                   default=False)
                cli.cmd_convert(_ns(config_path, remote=key, folder=folders,
                                    apply=apply, refresh=refresh))
            elif choice == "5":
                configure_pair(config_path)
            elif choice == "6":
                name = _pick_pair_name(config_path)
                if not name:
                    continue
                kind = ask_choice("Schedule type", ["every interval", "daily at time"],
                                  default="every interval")
                if kind == "every interval":
                    every = ask("Interval (e.g. 30m or 2h)", default="30m")
                    cli.cmd_schedule(_ns(config_path, pair=name, every=every, daily=None))
                else:
                    daily = ask("Time (HH:MM)", default="02:00")
                    cli.cmd_schedule(_ns(config_path, pair=name, every=None, daily=daily))
            elif choice == "7":
                name = _pick_pair_name(config_path)
                if name:
                    cli.cmd_unschedule(_ns(config_path, pair=name))
            elif choice == "8":
                cli.cmd_status(_ns(config_path))
            elif choice == "9":
                cli.cmd_check(_ns(config_path))
            elif choice == "10":
                cli.cmd_init(_ns(config_path, force=False))
            elif choice in ("h", "H", "help", "?"):
                print(HELP)
            elif choice in ("0", "q", "quit", "exit"):
                print("bye.")
                return 0
            else:
                print("  ? pick a number from the menu.")
        except Exception as exc:  # keep the menu alive on any action error
            print(f"  error: {exc}")
        input("\n[press Enter to return to the menu] ")
    return 0
