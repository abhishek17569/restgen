"""Load and validate restgen configuration files.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: Path) -> dict[str, Any]:
    """Load a YAML or JSON config file and return the raw dict.

    Args:
        config_path: Path to the config file (.yaml, .yml, or .json).

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If config_path does not exist.
        ValueError: If file extension is unsupported or content is invalid.

    Sig: 2026-04-14 created
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    suffix = config_path.suffix.lower()
    text = config_path.read_text(encoding="utf-8")

    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"Unsupported config format: {suffix}")

    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping, got {type(data).__name__}")

    return data
