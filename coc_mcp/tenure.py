"""Tenure data — days each player has been in our clan, by role.

The official Clash of Clans API does NOT expose tenure (days in clan). The only
sources are third-party sites like clashofstats.com which scrape the API and
archive history. This module:

  1. Provides a parser for COS player-summary page markdown
  2. Manages a local cache at snapshots/tenure/{tag_no_hash}.json
  3. Exposes read helpers for MCP tools

Refresh strategy: this MCP server doesn't scrape directly (Cloudflare blocks
naive GETs). Instead, refreshes happen via Claude (chat or scheduled task)
calling the crawl4ai MCP, which writes to the cache. See docs/SCHEDULED_TASK.md.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TENURE_DIR = PROJECT_ROOT / "snapshots" / "tenure"

# Patterns
_DURATION_RE = re.compile(
    r"(?:(\d+)\s*Years?\s*)?(?:(\d+)\s*Months?\s*)?(?:(\d+)\s*Days?)?",
    re.IGNORECASE,
)
_ROLE_LINE_RE = re.compile(
    r"^(Leader|Co-?leader|Elder|Admin|Member)\s*$",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(
    r"Total\s+((?:\d+\s*Years?\s*)?(?:\d+\s*Months?\s*)?(?:\d+\s*Days?))",
    re.IGNORECASE,
)


def parse_duration_to_days(text: str) -> int:
    """Convert '2 Months 30 Days' or '1 Year 5 Days' to total days.

    Months treated as 30 days, years as 365 (approximate — fine for elder rubric thresholds).
    """
    if not text:
        return 0
    m = _DURATION_RE.search(text.strip())
    if not m:
        return 0
    years = int(m.group(1) or 0)
    months = int(m.group(2) or 0)
    days = int(m.group(3) or 0)
    return years * 365 + months * 30 + days


def parse_tenure_markdown(markdown: str, our_clan_tag: str = "#YV9JRULU") -> Dict[str, Any]:
    """Parse a Clash of Stats player summary markdown blob into structured tenure data.

    Looks for the 'Player's role in Clan' block and the 'Player's Clans in the selected
    period' block. Returns:

        {
          "current_role": "Elder" | "Member" | ... | None,
          "role_breakdown": [{"role": "Member", "days": 90}, {"role": "Elder", "days": 1}],
          "total_days_in_current_clan": 91,
          "raw_role_section": "...",  # for debugging
        }
    """
    out: Dict[str, Any] = {
        "current_role": None,
        "role_breakdown": [],
        "total_days_in_current_clan": 0,
        "raw_role_section": "",
    }

    # The page text has a structure like:
    #   Player's role in Clan
    #   Member
    #   2 Months 30 Days
    #   Elder
    #   1 Day
    #   Player's Clans in the selected period
    #   [ Broken Arrow  - #YV9JRULU  Total 2 Months 30 Days  ...
    role_section_match = re.search(
        r"Player's role in Clan(.*?)Player's Clans in the selected period",
        markdown,
        re.DOTALL,
    )
    if role_section_match:
        section = role_section_match.group(1).strip()
        out["raw_role_section"] = section
        # Lines alternate: role name, duration string.
        lines = [ln.strip() for ln in section.split("\n") if ln.strip()]
        i = 0
        while i < len(lines) - 1:
            role_match = _ROLE_LINE_RE.match(lines[i])
            if role_match:
                role = role_match.group(1).title()
                if role.lower() == "co-leader" or role.lower() == "coleader":
                    role = "Co-leader"
                duration = lines[i + 1] if i + 1 < len(lines) else ""
                days = parse_duration_to_days(duration)
                out["role_breakdown"].append({"role": role, "days": days, "raw": duration})
                i += 2
            else:
                i += 1
        # Current role is the LAST entry in the breakdown (most recent).
        if out["role_breakdown"]:
            out["current_role"] = out["role_breakdown"][-1]["role"]

    # Total in current clan: pick the line containing our clan tag.
    clan_line_match = re.search(
        re.escape(our_clan_tag) + r"[^\n]*?" + _TOTAL_RE.pattern,
        markdown,
        re.IGNORECASE | re.DOTALL,
    )
    if clan_line_match:
        out["total_days_in_current_clan"] = parse_duration_to_days(clan_line_match.group(1))
    else:
        # Fallback: sum of role_breakdown days.
        out["total_days_in_current_clan"] = sum(r["days"] for r in out["role_breakdown"])

    return out


# --- Cache I/O -------------------------------------------------------------


def get_tenure_dir(snapshot_dir: Optional[Path] = None) -> Path:
    """Return the active tenure cache directory, creating it if needed."""
    base = (snapshot_dir or DEFAULT_TENURE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _normalize_tag(tag: str) -> str:
    """'#CR20YL9Q' -> 'CR20YL9Q'. Used for filenames."""
    return tag.lstrip("#").upper()


def write_tenure(
    player_tag: str,
    name: str,
    parsed: Dict[str, Any],
    snapshot_dir: Optional[Path] = None,
    api_current_role: Optional[str] = None,
) -> Path:
    """Write parsed tenure data to the cache.

    Args:
        player_tag: Player tag.
        name: Display name.
        parsed: Output of parse_tenure_markdown(); contains COS-derived role history.
        snapshot_dir: Cache directory override.
        api_current_role: If provided, stored as `api_current_role` — authoritative
            role from the live COC API. Use this for any "what role is this player"
            question; the COS-derived `current_role` can lag or misorder.
    """
    base = get_tenure_dir(snapshot_dir)
    payload = {
        "tag": "#" + _normalize_tag(player_tag),
        "name": name,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    if api_current_role is not None:
        payload["api_current_role"] = api_current_role
    path = base / f"{_normalize_tag(player_tag)}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def update_api_role(player_tag: str, api_role: str, snapshot_dir: Optional[Path] = None) -> Optional[Path]:
    """Update the api_current_role field on an existing cache entry.

    Returns the path written to, or None if the entry doesn't exist yet.
    """
    base = get_tenure_dir(snapshot_dir)
    path = base / f"{_normalize_tag(player_tag)}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    data["api_current_role"] = api_role
    data["api_role_updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2))
    return path


def read_tenure(player_tag: str, snapshot_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read cached tenure for a player. Returns None if not cached."""
    base = get_tenure_dir(snapshot_dir)
    path = base / f"{_normalize_tag(player_tag)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def list_cached_tenure(snapshot_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List all cached tenure entries."""
    base = get_tenure_dir(snapshot_dir)
    out: List[Dict[str, Any]] = []
    for p in sorted(base.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out
