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


def _print_pair(pr: dict, remotes: dict) -> None:
    lkey, rkey = pr.get("left"), pr.get("right")
    print("\n  ---------------------------------------------")
    print(f"  pair    : {pr.get('name')}")
    print(f"  source  : {lkey} -> {remotes.get(lkey, '?')}")
    print(f"  target  : {rkey} -> {remotes.get(rkey, '?')}")
    print(f"  mode    : {pr.get('mode')}")
    print(f"  scope   : {pr.get('scope')}")
    if pr.get("scope") == "folders":
        print(f"  folders : {', '.join(pr.get('folders') or []) or '(none)'}")
    print(f"  since   : {(pr.get('delta') or {}).get('since')}")
    print("  ---------------------------------------------")


def _edit_pair(pr: dict, remotes: dict, available: List[str]) -> None:
    for side in ("left", "right"):
        cur_key = pr.get(side)
        cur_remote = remotes.get(cur_key) if cur_key else None
        label = "Source cloud remote" if side == "left" else "Target cloud remote"
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
========== tidysync : Drive <-> OneDrive ==========
  1) Run a sync pair
  2) Run all pairs
  3) Find duplicates in a cloud (dedupe)
  4) Configure / edit a sync pair
  5) Schedule a pair   6) Unschedule a pair
  7) Status            8) Check remotes
  9) Create/repair config (init)
  0) Quit
===================================================
"""


def _ns(config_path, **kw):
    return SimpleNamespace(config=str(config_path), yes=True, **kw)


def _pick_pair_name(config_path) -> Optional[str]:
    raw = load_raw(config_path)
    names = [p.get("name") for p in (raw.get("pairs") or [])]
    if not names:
        print("  No pairs configured yet — use option 4 to add one.")
        return None
    return ask_choice("Which pair?", names, default=names[0])


def _pick_remote_key(config_path) -> Optional[str]:
    raw = load_raw(config_path)
    keys = list((raw.get("remotes") or {}).keys())
    if not keys:
        print("  No remotes configured — use option 4 or edit the config.")
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
                            dry_run=True if dry else None)
            elif choice == "2":
                dry = ask_yes_no("Dry run for all pairs?", default=False)
                cli.cmd_run_all(_ns(config_path, since=None, dry_run=dry))
            elif choice == "3":
                key = _pick_remote_key(config_path)
                if not key:
                    continue
                apply = ask_yes_no("Move duplicates to quarantine now? "
                                   "(No = report only)", default=False)
                cli.cmd_dedupe(_ns(config_path, remote=key, folder=None,
                                   apply=apply, quarantine=dedupe.QUARANTINE_DIR))
            elif choice == "4":
                configure_pair(config_path)
            elif choice == "5":
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
            elif choice == "6":
                name = _pick_pair_name(config_path)
                if name:
                    cli.cmd_unschedule(_ns(config_path, pair=name))
            elif choice == "7":
                cli.cmd_status(_ns(config_path))
            elif choice == "8":
                cli.cmd_check(_ns(config_path))
            elif choice == "9":
                cli.cmd_init(_ns(config_path, force=False))
            elif choice in ("0", "q", "quit", "exit"):
                print("bye.")
                return 0
            else:
                print("  ? pick a number from the menu.")
        except Exception as exc:  # keep the menu alive on any action error
            print(f"  error: {exc}")
        input("\n[press Enter to return to the menu] ")
    return 0
