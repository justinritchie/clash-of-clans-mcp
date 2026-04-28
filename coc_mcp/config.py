"""Config loading: token from env, rubric from JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUBRIC_PATH = REPO_ROOT / "config" / "rubric.default.json"


def get_api_token() -> str:
    """Get the COC API token from env. Raises if missing."""
    token = os.environ.get("COC_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "COC_API_TOKEN environment variable is not set. "
            "Get a token at https://developer.clashofclans.com (your IP must be whitelisted)."
        )
    return token


def get_default_clan_tag() -> Optional[str]:
    """Get the default clan tag if configured (e.g. '#YV9JRULU'). May be None."""
    tag = os.environ.get("COC_DEFAULT_CLAN_TAG", "").strip()
    return tag or None


def get_rubric_path() -> Path:
    """Get path to the active rubric file. Honors COC_RUBRIC_PATH override."""
    override = os.environ.get("COC_RUBRIC_PATH", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path
    return DEFAULT_RUBRIC_PATH


def load_rubric(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and return the rubric JSON as a dict."""
    rubric_path = path or get_rubric_path()
    if not rubric_path.exists():
        raise FileNotFoundError(
            f"Rubric file not found at {rubric_path}. "
            f"Copy config/rubric.default.json to that path or set COC_RUBRIC_PATH."
        )
    with rubric_path.open() as f:
        return json.load(f)
