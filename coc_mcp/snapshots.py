"""Local war-snapshot store.

The official Clash of Clans API only exposes the *current* war. Once a new war
starts, the previous war's per-attack details are gone. This module saves war
JSON to disk so we can build long-term per-player history.

Designed to be safe to call repeatedly (idempotent, deduped by `endTime`) so
that a scheduled Claude task can invoke it every couple of days without
producing duplicates or losing data if a snapshot was missed.

Storage layout:

    ~/.coc-mcp/snapshots/
      wars/                    # regular wars
        20260428T102104_clash_serious_20YJ8J9VR.json
        20260426T061239_thien_nga_trang_2GYP8LCG2.json
        ...
      cwl/                     # CWL wars (one file per round war)
        2026-04/
          {warTag}.json
      cwl_groups/              # CWL group metadata
        2026-04.json
      index.json               # quick-lookup map of all snapshots

Env: COC_SNAPSHOT_DIR overrides the default location.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "snapshots"


def get_snapshot_dir() -> Path:
    """Return the active snapshot directory, creating it if needed."""
    override = os.environ.get("COC_SNAPSHOT_DIR", "").strip()
    base = Path(override).expanduser() if override else DEFAULT_SNAPSHOT_DIR
    (base / "wars").mkdir(parents=True, exist_ok=True)
    (base / "cwl").mkdir(parents=True, exist_ok=True)
    (base / "cwl_groups").mkdir(parents=True, exist_ok=True)
    return base


def _slugify(name: str, max_len: int = 40) -> str:
    """Make a name safe for filenames."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "unknown").lower()).strip("_")
    return slug[:max_len] or "unknown"


def _war_filename(war: Dict[str, Any]) -> str:
    """Build a stable filename for a regular war snapshot."""
    end_time = war.get("endTime", "unknown")
    opp = war.get("opponent", {}) or {}
    opp_tag = (opp.get("tag") or "no_tag").lstrip("#")
    opp_name = _slugify(opp.get("name") or "unknown")
    # Strip dots from endTime for cleaner filename: "20260428T102104.000Z" -> "20260428T102104"
    end_clean = end_time.split(".")[0] if "." in end_time else end_time
    return f"{end_clean}_{opp_name}_{opp_tag}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wrap_with_metadata(war: Dict[str, Any], source: str, version: str = "0.3.0") -> Dict[str, Any]:
    """Wrap raw war JSON with capture metadata at the top of the file."""
    return {
        "_snapshot_metadata": {
            "captured_at": _now_iso(),
            "source": source,
            "tool_version": version,
        },
        **war,
    }


def snapshot_regular_war(
    war: Dict[str, Any],
    *,
    force: bool = False,
    snapshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Save a regular-war snapshot to disk.

    Args:
        war: Full JSON from /clans/{tag}/currentwar.
        force: If True, save even if state != "warEnded" (for partial snapshots).
        snapshot_dir: Override the snapshot directory (defaults to env or ~).

    Returns:
        {
          "snapshotted": bool,
          "reason": str,
          "path": str | None,
          "war_id": str (endTime),
        }
    """
    base = snapshot_dir or get_snapshot_dir()
    wars_dir = base / "wars"

    state = war.get("state", "")
    if state == "notInWar":
        return {"snapshotted": False, "reason": "state=notInWar; no war to snapshot.", "path": None, "war_id": None}
    if state != "warEnded" and not force:
        return {
            "snapshotted": False,
            "reason": f"state={state}; refusing to snapshot in-progress war (use force=True to override).",
            "path": None,
            "war_id": war.get("endTime"),
        }

    fname = _war_filename(war)
    path = wars_dir / fname

    if path.exists() and not force:
        return {
            "snapshotted": False,
            "reason": "Already snapshotted (deduped by endTime+opponent).",
            "path": str(path),
            "war_id": war.get("endTime"),
        }

    payload = _wrap_with_metadata(war, source="clash_snapshot_war")
    path.write_text(json.dumps(payload, indent=2))

    _update_index(base)

    return {
        "snapshotted": True,
        "reason": "Saved.",
        "path": str(path),
        "war_id": war.get("endTime"),
    }


def snapshot_cwl_war(
    war: Dict[str, Any],
    *,
    season: str,
    war_tag: str,
    snapshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Save a CWL round war to disk."""
    base = snapshot_dir or get_snapshot_dir()
    season_dir = base / "cwl" / season
    season_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{war_tag.lstrip('#')}.json"
    path = season_dir / fname

    if path.exists():
        return {
            "snapshotted": False,
            "reason": "Already snapshotted.",
            "path": str(path),
            "war_id": war_tag,
        }

    payload = _wrap_with_metadata(war, source="clash_snapshot_cwl_war")
    path.write_text(json.dumps(payload, indent=2))
    return {
        "snapshotted": True,
        "reason": "Saved.",
        "path": str(path),
        "war_id": war_tag,
    }


def list_snapshots(snapshot_dir: Optional[Path] = None) -> Dict[str, Any]:
    """List all stored snapshots."""
    base = snapshot_dir or get_snapshot_dir()
    wars = sorted([p.name for p in (base / "wars").glob("*.json")])
    cwl_seasons = sorted([d.name for d in (base / "cwl").iterdir() if d.is_dir()])
    cwl_wars: Dict[str, List[str]] = {}
    for season in cwl_seasons:
        cwl_wars[season] = sorted([p.name for p in (base / "cwl" / season).glob("*.json")])
    return {
        "snapshot_dir": str(base),
        "regular_wars": wars,
        "regular_war_count": len(wars),
        "cwl_seasons": cwl_seasons,
        "cwl_wars_by_season": cwl_wars,
        "cwl_war_count": sum(len(v) for v in cwl_wars.values()),
    }


def reconcile_with_warlog(
    warlog: Dict[str, Any],
    *,
    snapshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare warlog (summary) against snapshot store. Identify gaps.

    Returns a list of wars we know happened (per the warlog) but for which we
    have no full snapshot. These are unrecoverable — the API doesn't expose
    per-attack data for past wars — but reporting the gap is useful.

    Args:
        warlog: Output of /clans/{tag}/warlog.
    """
    base = snapshot_dir or get_snapshot_dir()
    # Index snapshots by (opponent_tag, end_date) — endTime can drift by a few seconds
    # between /currentwar and /warlog, so we match on opponent tag + day.
    snapshotted: set = set()
    for p in (base / "wars").glob("*.json"):
        try:
            data = json.loads(p.read_text())
            opp_tag = (data.get("opponent", {}) or {}).get("tag")
            et = data.get("endTime", "")
            day = et[:8] if et else ""  # YYYYMMDD
            if opp_tag:
                snapshotted.add((opp_tag, day))
        except Exception:
            continue

    items = warlog.get("items", []) or []
    gaps: List[Dict[str, Any]] = []
    matched = 0
    for war in items:
        et = (war.get("endTime") or "")
        day = et[:8]
        opp = war.get("opponent", {}) or {}
        opp_tag = opp.get("tag")
        if opp_tag and (opp_tag, day) in snapshotted:
            matched += 1
        else:
            gaps.append({
                "end_time": war.get("endTime"),
                "result": war.get("result"),
                "opponent_name": opp.get("name"),
                "opponent_tag": opp.get("tag"),
                "stars": war.get("clan", {}).get("stars"),
                "destruction": war.get("clan", {}).get("destructionPercentage"),
                "attacks_per_member": war.get("attacksPerMember"),
                "team_size": war.get("teamSize"),
            })

    return {
        "warlog_total": len(items),
        "snapshotted_count": matched,
        "gap_count": len(gaps),
        "gaps": gaps,
        "note": (
            "Gap wars are unrecoverable — the COC API doesn't expose per-attack data "
            "for past wars. Going forward, run snapshot_war within ~2 days of war end "
            "to avoid creating new gaps."
        ) if gaps else "No gaps — snapshot store is in sync with the warlog window.",
    }


def _update_index(base: Path) -> None:
    """Maintain a lightweight index.json for fast lookups."""
    idx: Dict[str, Any] = {
        "updated_at": _now_iso(),
        "regular_wars": [],
        "cwl_wars": [],
    }
    for p in sorted((base / "wars").glob("*.json")):
        try:
            data = json.loads(p.read_text())
            idx["regular_wars"].append({
                "file": p.name,
                "end_time": data.get("endTime"),
                "opponent_name": data.get("opponent", {}).get("name"),
                "opponent_tag": data.get("opponent", {}).get("tag"),
                "result": _derive_result(data),
            })
        except Exception:
            continue
    for season_dir in sorted((base / "cwl").iterdir()):
        if not season_dir.is_dir():
            continue
        for p in sorted(season_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                idx["cwl_wars"].append({
                    "season": season_dir.name,
                    "file": p.name,
                    "end_time": data.get("endTime"),
                    "clan_name": data.get("clan", {}).get("name"),
                    "opponent_name": data.get("opponent", {}).get("name"),
                })
            except Exception:
                continue
    (base / "index.json").write_text(json.dumps(idx, indent=2))


def _derive_result(war: Dict[str, Any]) -> str:
    if war.get("state") != "warEnded":
        return war.get("state", "unknown")
    our = war.get("clan", {})
    them = war.get("opponent", {})
    if our.get("stars", 0) > them.get("stars", 0):
        return "win"
    if our.get("stars", 0) < them.get("stars", 0):
        return "lose"
    o, t = our.get("destructionPercentage", 0), them.get("destructionPercentage", 0)
    if o > t:
        return "win"
    if o < t:
        return "lose"
    return "tie"


def player_war_history(
    player_tag: str,
    *,
    n: int = 10,
    snapshot_dir: Optional[Path] = None,
    our_clan_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a player's per-war attack history from the snapshot store.

    Walks snapshots newest-first, finds the player's attacks in each, returns
    a flat timeline with computed aggregates.

    Args:
        player_tag: With or without leading '#'.
        n: Max number of wars to include (default 10).
        our_clan_tag: If provided, picks the matching side for each war.
    """
    if not player_tag.startswith("#"):
        player_tag = "#" + player_tag.upper()
    else:
        player_tag = player_tag.upper()

    base = snapshot_dir or get_snapshot_dir()
    files = sorted((base / "wars").glob("*.json"), reverse=True)[:n]

    history: List[Dict[str, Any]] = []
    total_stars = 0
    total_destruction = 0.0
    total_attacks_used = 0
    total_attacks_owed = 0

    for fp in files:
        try:
            war = json.loads(fp.read_text())
        except Exception:
            continue

        # Pick our side.
        if our_clan_tag and war.get("opponent", {}).get("tag") == our_clan_tag:
            our_side, their_side = war["opponent"], war["clan"]
        else:
            our_side, their_side = war.get("clan", {}), war.get("opponent", {})

        member = next((m for m in (our_side.get("members") or []) if m.get("tag", "").upper() == player_tag), None)
        if member is None:
            continue  # player wasn't in this war

        their_pos = {m["tag"]: m["mapPosition"] for m in (their_side.get("members") or [])}
        attacks_per = war.get("attacksPerMember", 2)
        attacks = sorted((member.get("attacks") or []), key=lambda a: a.get("order", 0))
        used = len(attacks)
        missed = max(0, attacks_per - used)

        attack_records = []
        for seq, atk in enumerate(attacks, start=1):
            d_pos = their_pos.get(atk.get("defenderTag"))
            attack_records.append({
                "seq": seq,
                "target_pos": d_pos,
                "stars": atk.get("stars"),
                "destruction": atk.get("destructionPercentage"),
                "offset": (d_pos - member.get("mapPosition")) if (d_pos and member.get("mapPosition")) else None,
            })
            total_stars += atk.get("stars", 0)
            total_destruction += atk.get("destructionPercentage", 0)
            total_attacks_used += 1

        total_attacks_owed += attacks_per

        history.append({
            "end_time": war.get("endTime"),
            "opponent_name": their_side.get("name"),
            "result": _derive_result(war),
            "player_pos": member.get("mapPosition"),
            "player_th": member.get("townhallLevel"),
            "attacks_used": used,
            "attacks_missed": missed,
            "attacks": attack_records,
        })

    if total_attacks_used == 0:
        avg_stars = 0.0
        avg_destruction = 0.0
    else:
        avg_stars = round(total_stars / total_attacks_used, 2)
        avg_destruction = round(total_destruction / total_attacks_used, 1)

    return {
        "player_tag": player_tag,
        "wars_found": len(history),
        "aggregate": {
            "total_attacks_used": total_attacks_used,
            "total_attacks_owed": total_attacks_owed,
            "attendance_pct": round(total_attacks_used / total_attacks_owed * 100, 1) if total_attacks_owed else 0.0,
            "avg_stars_per_attack": avg_stars,
            "avg_destruction_per_attack": avg_destruction,
        },
        "wars": history,
    }
