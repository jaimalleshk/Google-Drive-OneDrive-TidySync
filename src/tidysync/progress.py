"""Tiny terminal progress helpers (bar / elapsed / ETA / counts) for our own loops.

rclone's own `--progress` covers the transfer phase. This Counter is for the steps we
drive ourselves (dedupe moves, Google-doc conversion) where we know the total upfront,
so it can show a real percentage progress bar with done/total + elapsed + ETA.
Bar uses ASCII characters so the Windows console never chokes on encoding.
"""

from __future__ import annotations

import sys
import time


def fmt_secs(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


def bar(frac: float, width: int = 22) -> str:
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    filled = int(round(frac * width))
    return "#" * filled + "-" * (width - filled)


class Counter:
    def __init__(self, total: int, label: str = "", on: bool = True):
        self.total = max(int(total), 0)
        self.label = label
        self.on = on and self.total > 0
        self.start = time.time()
        self.started_str = time.strftime("%H:%M:%S", time.localtime(self.start))
        self.done = 0
        if self.on:
            sys.stderr.write(f"  {self.label}: {self.total} files, started {self.started_str}\n")
            sys.stderr.flush()

    def step(self, name: str = "") -> None:
        self.done += 1
        if not self.on:
            return
        elapsed = time.time() - self.start
        frac = self.done / self.total if self.total else 0.0
        rate = self.done / elapsed if elapsed > 0 else 0.0
        eta = (self.total - self.done) / rate if rate > 0 else 0.0
        short = name[-34:] if len(name) > 34 else name
        sys.stderr.write(
            f"\r  [{bar(frac)}] {int(frac * 100):3d}%  {self.done}/{self.total}  "
            f"elapsed {fmt_secs(elapsed)}  eta {fmt_secs(eta)}  {short:<36}")
        sys.stderr.flush()

    def close(self) -> None:
        if self.on:
            elapsed = fmt_secs(time.time() - self.start)
            sys.stderr.write("\r" + " " * 92 + "\r")
            sys.stderr.write(f"  {self.label}: {self.done}/{self.total} done in {elapsed}\n")
            sys.stderr.flush()
