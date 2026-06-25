"""Load and validate the YAML configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

VALID_MODES = {"left-to-right", "right-to-left", "two-way"}
VALID_SCOPES = {"whole-drive", "folders"}


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass
class PairConfig:
    name: str
    left: str          # key into remotes
    right: str         # key into remotes
    mode: str
    scope: str
    folders: List[str] = field(default_factory=list)
    since: str = "last-sync"
    filters: List[str] = field(default_factory=list)
    dry_run: bool = False
    # Convert Google Workspace docs to Office files on the Drive side before syncing.
    convert_google_docs: bool = True
    # Extra rclone flags (e.g. throttling for API rate limits) appended to list/copy.
    rclone_args: List[str] = field(default_factory=list)

    # resolved rclone remote strings, filled in by AppConfig
    left_remote: str = ""
    right_remote: str = ""


@dataclass
class AppConfig:
    base_dir: Path                 # directory containing the config file
    remotes: Dict[str, str]
    pairs: Dict[str, PairConfig]

    @property
    def state_dir(self) -> Path:
        return self.base_dir / "state"

    @property
    def reports_dir(self) -> Path:
        return self.base_dir / "reports"

    def pair(self, name: str) -> PairConfig:
        if name not in self.pairs:
            known = ", ".join(self.pairs) or "(none)"
            raise ConfigError(f"No sync pair named '{name}'. Configured pairs: {known}")
        return self.pairs[name]


def default_config_path() -> Path:
    env = os.environ.get("TIDYSYNC_CONFIG")
    if env:
        return Path(env)
    return Path.cwd() / "config.yaml"


def load_raw(path: Path) -> dict:
    """Load the raw YAML mapping (no validation). Missing file -> empty dict."""
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_raw(path: Path, data: dict) -> None:
    """Write the raw YAML mapping back to disk.

    Note: this rewrites the file via PyYAML, so hand-written comments are not
    preserved. Field order is kept (sort_keys=False).
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)


def load_config(path: Path) -> AppConfig:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Run `tidysync init` to create one from the template."
        )
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    remotes = raw.get("remotes") or {}
    if not isinstance(remotes, dict) or not remotes:
        raise ConfigError("Config must define a non-empty 'remotes' mapping.")
    for key, val in remotes.items():
        if not isinstance(val, str) or not val.endswith(":") and ":" not in val:
            raise ConfigError(
                f"Remote '{key}' must be an rclone remote string ending in ':' "
                f"(e.g. 'gdrive:'). Got: {val!r}"
            )

    pairs_raw = raw.get("pairs") or []
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise ConfigError("Config must define a non-empty 'pairs' list.")

    pairs: Dict[str, PairConfig] = {}
    for i, p in enumerate(pairs_raw):
        if not isinstance(p, dict):
            raise ConfigError(f"pairs[{i}] must be a mapping.")
        name = p.get("name")
        if not name:
            raise ConfigError(f"pairs[{i}] is missing 'name'.")
        if name in pairs:
            raise ConfigError(f"Duplicate pair name: '{name}'.")

        mode = p.get("mode", "two-way")
        if mode not in VALID_MODES:
            raise ConfigError(
                f"Pair '{name}': invalid mode '{mode}'. Use one of {sorted(VALID_MODES)}."
            )
        scope = p.get("scope", "folders")
        if scope not in VALID_SCOPES:
            raise ConfigError(
                f"Pair '{name}': invalid scope '{scope}'. Use one of {sorted(VALID_SCOPES)}."
            )
        left = p.get("left")
        right = p.get("right")
        for side, val in (("left", left), ("right", right)):
            if val not in remotes:
                raise ConfigError(
                    f"Pair '{name}': {side} remote '{val}' is not defined in 'remotes'."
                )
        folders = p.get("folders") or []
        if scope == "folders" and not folders:
            raise ConfigError(
                f"Pair '{name}': scope is 'folders' but no 'folders' were listed."
            )

        delta = p.get("delta") or {}
        since = delta.get("since", "last-sync")

        pair = PairConfig(
            name=name,
            left=left,
            right=right,
            mode=mode,
            scope=scope,
            folders=[str(f).strip().strip("/") for f in folders],
            since=str(since),
            filters=[str(f) for f in (p.get("filters") or [])],
            dry_run=bool(p.get("dry_run", False)),
            convert_google_docs=bool(p.get("convert_google_docs", True)),
            rclone_args=[str(a) for a in (p.get("rclone_args") or [])],
        )
        pair.left_remote = remotes[left]
        pair.right_remote = remotes[right]
        pairs[name] = pair

    return AppConfig(base_dir=path.parent, remotes=remotes, pairs=pairs)
