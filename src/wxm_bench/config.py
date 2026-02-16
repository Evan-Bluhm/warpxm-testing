"""Configuration file support for wxm-bench."""

import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "wxm-bench.toml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load configuration from a TOML file.

    Returns an empty dict if the file does not exist.
    Expands ~ in path values under [paths].
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}

    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    # Expand ~ in path values
    if "paths" in cfg:
        for key, val in cfg["paths"].items():
            if isinstance(val, str):
                cfg["paths"][key] = str(Path(val).expanduser())

    return cfg


def get(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely access nested dict keys.

    Example: get(cfg, "paths", "source_dir", default="/fallback")
    """
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
