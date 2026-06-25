"""Register/unregister scheduled runs via Windows Task Scheduler (schtasks)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple


class ScheduleError(Exception):
    pass


def task_name(pair: str) -> str:
    return f"tidysync_{pair}"


def _parse_every(every: str) -> Tuple[str, int]:
    """'30m' -> ('MINUTE', 30); '2h' -> ('HOURLY', 2); '45' -> ('MINUTE', 45)."""
    every = every.strip().lower()
    if every.endswith("h"):
        return "HOURLY", int(every[:-1])
    if every.endswith("m"):
        return "MINUTE", int(every[:-1])
    return "MINUTE", int(every)


def _run_command(pair: str, config_path: Path) -> str:
    """Command string the scheduled task executes."""
    py = Path(sys.executable)
    # pythonw.exe runs without a console window for background scheduling.
    pyw = py.with_name("pythonw.exe")
    exe = pyw if pyw.exists() else py
    cfg = Path(config_path).resolve()
    # --yes keeps the scheduled run fully non-interactive (no confirmation prompts).
    return f'"{exe}" -m tidysync --yes --config "{cfg}" run {pair}'


def create(pair: str, config_path: Path,
           every: Optional[str] = None, daily: Optional[str] = None) -> str:
    if bool(every) == bool(daily):
        raise ScheduleError("Specify exactly one of --every or --daily.")
    tn = task_name(pair)
    tr = _run_command(pair, config_path)
    cmd = ["schtasks", "/Create", "/TN", tn, "/TR", tr, "/F"]
    if every:
        sc, mo = _parse_every(every)
        cmd += ["/SC", sc]
        if sc == "MINUTE":
            cmd += ["/MO", str(mo)]
        elif sc == "HOURLY":
            cmd += ["/MO", str(mo)]
    else:
        cmd += ["/SC", "DAILY", "/ST", daily]

    out = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        raise ScheduleError(out.stderr.strip() or out.stdout.strip() or "schtasks failed")
    return tn


def delete(pair: str) -> str:
    tn = task_name(pair)
    out = subprocess.run(
        ["schtasks", "/Delete", "/TN", tn, "/F"],
        capture_output=True, text=True, errors="replace",
    )
    if out.returncode != 0:
        raise ScheduleError(out.stderr.strip() or out.stdout.strip() or "schtasks failed")
    return tn


def query(pair: str) -> str:
    out = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name(pair)],
        capture_output=True, text=True, errors="replace",
    )
    return out.stdout.strip() if out.returncode == 0 else (out.stderr.strip() or "not found")
