"""Per-pair last-sync timestamp store (one JSON file under state/)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def utcnow_iso() -> str:
    """Current UTC time as an RFC3339 string rclone understands."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pair: str) -> Path:
        return self.state_dir / f"{pair}.json"

    def get_last_sync(self, pair: str) -> Optional[str]:
        path = self._path(pair)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data.get("last_sync")

    def set_last_sync(self, pair: str, ts_iso: str, report: Optional[str] = None) -> None:
        path = self._path(pair)
        data = {"pair": pair, "last_sync": ts_iso}
        if report:
            data["last_report"] = report
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_last_report(self, pair: str) -> Optional[str]:
        path = self._path(pair)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("last_report")
        except (json.JSONDecodeError, OSError):
            return None
