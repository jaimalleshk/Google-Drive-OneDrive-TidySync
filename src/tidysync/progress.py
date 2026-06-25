"""Tiny terminal progress helpers (elapsed / ETA / counts) for our own loops.

rclone's own `--progress` covers the transfer phase (%, speed, ETA, files). This
Counter is for the steps we drive ourselves: Google-doc conversion and dedupe moves,
where we know the total upfront and can show done/pending + elapsed + estimated time.
"""

from __future__ import annotations

import sys
import time


class Counter:
    def __init__(self, total: int, label: str = "", on: bool = True):
        self.total = max(int(total), 0)
        self.label = label
        self.on = on and self.total > 0
        self.start = time.time()
        self.done = 0

    def step(self, name: str = "") -> None:
        self.done += 1
        if not self.on:
            return
        elapsed = time.time() - self.start
        rate = self.done / elapsed if elapsed > 0 else 0.0
        eta = (self.total - self.done) / rate if rate > 0 else 0.0
        short = (name[:38] + "...") if len(name) > 41 else name
        sys.stderr.write(
            f"\r  [{self.done}/{self.total}] {self.label}  "
            f"elapsed {int(elapsed)}s, ~{int(eta)}s left  {short:<42}")
        sys.stderr.flush()

    def close(self) -> None:
        if self.on:
            elapsed = int(time.time() - self.start)
            sys.stderr.write("\r" + " " * 92 + "\r")
            sys.stderr.write(f"  {self.label}: {self.done}/{self.total} in {elapsed}s\n")
            sys.stderr.flush()
