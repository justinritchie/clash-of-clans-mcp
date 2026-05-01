"""In-war status helpers — for analyzing a war that's still in progress.

The grading/reporting tools assume a finished war and frame everything as
post-mortem. This module reframes the same data for mid-war: pending attackers,
time remaining, score gap, win projection.

Works for state in: 'preparation', 'inWar', 'warEnded'. Returns clear
"war hasn't started" / "war is over" markers as appropriate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _parse_coc_time(s: Optional[str]) -> Optional[datetime]:
    """Parse a COC API timestamp like '20260428T102104.000Z' to UTC datetime."""
    if not s:
        return None
    # COC times are like 'YYYYMMDDTHHMMSS.000Z'
    try:
        return datetime.strptime(s.split(".")[0], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    if seconds < 0:
        return "ended"
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {s}s"
    hours, m = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {m}m"
    days, h = divmod(hours, 24)
    return f"{days}d {h}h"


def in_war_status(
    war: Dict[str, Any],
    *,
    our_clan_tag: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a mid-war status snapshot.

    Returns:
        {
          "state": str,
          "is_active": bool,
          "time_remaining_seconds": int | None,
          "time_remaining_pretty": str,
          "score": {
            "us": {"name", "stars", "destruction", "attacks_used", "attacks_total"},
            "them": {"name", "stars", "destruction", "attacks_used", "attacks_total"},
            "gap_stars": int,            # us - them
            "gap_destruction": float,    # us - them
            "leading": "us" | "them" | "tied",
          },
          "pending_attackers": [...],     # players who still owe attacks
          "completed_attackers": [...],   # players who used all their attacks
          "projection": {
            "avg_stars_per_attack_so_far": float,
            "expected_stars_if_all_remaining_attackers_match_avg": float,
            "expected_final_stars": float,
            "would_win_at_current_pace": bool | None,
          }
        }
    """
    now = now or datetime.now(timezone.utc)
    state = war.get("state", "unknown")

    if state in ("notInWar", None):
        return {"state": state, "is_active": False, "message": "Clan is not currently in a war."}

    # Pick our side.
    if our_clan_tag and war.get("opponent", {}).get("tag") == our_clan_tag:
        us, them = war["opponent"], war["clan"]
    else:
        us = war.get("clan", {}) or {}
        them = war.get("opponent", {}) or {}

    attacks_per_member = war.get("attacksPerMember", 2)
    team_size = war.get("teamSize", len(us.get("members", []) or []))
    attacks_total = team_size * attacks_per_member

    us_attacks_used = sum(len(m.get("attacks") or []) for m in (us.get("members") or []))
    them_attacks_used = sum(len(m.get("attacks") or []) for m in (them.get("members") or []))

    us_stars = us.get("stars", 0)
    them_stars = them.get("stars", 0)
    us_dest = us.get("destructionPercentage", 0.0)
    them_dest = them.get("destructionPercentage", 0.0)

    # Time remaining.
    end_time = _parse_coc_time(war.get("endTime"))
    start_time = _parse_coc_time(war.get("startTime"))
    prep_start = _parse_coc_time(war.get("preparationStartTime"))
    seconds_remaining: Optional[int] = None
    time_label = "n/a"
    if state == "preparation" and start_time:
        delta = int((start_time - now).total_seconds())
        seconds_remaining = max(0, delta)
        time_label = f"war starts in {_format_duration(seconds_remaining)}"
    elif state == "inWar" and end_time:
        delta = int((end_time - now).total_seconds())
        seconds_remaining = max(0, delta)
        time_label = f"{_format_duration(seconds_remaining)} until war ends"
    elif state == "warEnded":
        time_label = "war ended"

    # Pending vs completed attackers.
    pending: List[Dict[str, Any]] = []
    completed: List[Dict[str, Any]] = []
    for m in (us.get("members") or []):
        used = len(m.get("attacks") or [])
        owed = max(0, attacks_per_member - used)
        record = {
            "tag": m.get("tag"),
            "name": m.get("name"),
            "map_position": m.get("mapPosition"),
            "th": m.get("townhallLevel"),
            "attacks_used": used,
            "attacks_owed": owed,
        }
        if owed > 0:
            pending.append(record)
        else:
            completed.append(record)
    pending.sort(key=lambda p: p.get("map_position") or 999)
    completed.sort(key=lambda p: p.get("map_position") or 999)

    # Projection.
    pending_attacks = sum(p["attacks_owed"] for p in pending)
    avg_stars = (us_stars / us_attacks_used) if us_attacks_used else 0.0
    projected_extra = avg_stars * pending_attacks
    projected_final = us_stars + projected_extra
    would_win: Optional[bool] = None
    if state == "inWar":
        # Simple model: assume opponent also performs at their current avg.
        them_pending = max(0, attacks_total - them_attacks_used)
        them_avg = (them_stars / them_attacks_used) if them_attacks_used else 0.0
        them_projected_final = them_stars + them_avg * them_pending
        would_win = projected_final > them_projected_final
    elif state == "warEnded":
        would_win = us_stars > them_stars

    leading = "us" if us_stars > them_stars else ("them" if them_stars < us_stars else "tied")
    if us_stars == them_stars:
        if us_dest > them_dest:
            leading = "us"
        elif us_dest < them_dest:
            leading = "them"
        else:
            leading = "tied"

    return {
        "state": state,
        "is_active": state in ("preparation", "inWar"),
        "time_remaining_seconds": seconds_remaining,
        "time_remaining_pretty": time_label,
        "score": {
            "us": {
                "name": us.get("name"),
                "stars": us_stars,
                "destruction": round(us_dest, 2),
                "attacks_used": us_attacks_used,
                "attacks_total": attacks_total,
            },
            "them": {
                "name": them.get("name"),
                "stars": them_stars,
                "destruction": round(them_dest, 2),
                "attacks_used": them_attacks_used,
                "attacks_total": attacks_total,
            },
            "gap_stars": us_stars - them_stars,
            "gap_destruction": round(us_dest - them_dest, 2),
            "leading": leading,
        },
        "pending_attackers": pending,
        "completed_attackers": completed,
        "projection": {
            "avg_stars_per_attack_so_far": round(avg_stars, 2),
            "pending_attacks": pending_attacks,
            "expected_extra_stars": round(projected_extra, 1),
            "expected_final_stars": round(projected_final, 1),
            "would_win_at_current_pace": would_win,
        },
    }


def in_war_status_markdown(status: Dict[str, Any]) -> str:
    """Render an in-war status dict as a leadership-friendly markdown digest."""
    if not status.get("is_active") and status.get("state") == "notInWar":
        return "_Clan is not currently in a war._"

    state = status["state"]
    score = status["score"]
    us = score["us"]
    them = score["them"]
    pending = status["pending_attackers"]
    completed = status["completed_attackers"]
    proj = status["projection"]

    lines: List[str] = []
    state_label = {"preparation": "🛡️ Preparation Day", "inWar": "⚔️ Battle Day", "warEnded": "🏁 War Ended"}.get(state, state)
    lines.append(f"# {state_label} — {us['name']} vs {them['name']}")
    lines.append("")
    lines.append(f"⏱️  **{status['time_remaining_pretty']}**")
    lines.append("")
    lines.append(f"## Score")
    lines.append("")
    lines.append(f"| | {us['name']} (us) | {them['name']} (them) |")
    lines.append("|---|---|---|")
    lines.append(f"| ⭐ Stars | **{us['stars']}** | {them['stars']} |")
    lines.append(f"| 💥 Destruction | {us['destruction']:.1f}% | {them['destruction']:.1f}% |")
    lines.append(f"| Attacks used | {us['attacks_used']} / {us['attacks_total']} | {them['attacks_used']} / {them['attacks_total']} |")
    lines.append("")
    leading = score["leading"]
    if leading == "us":
        lead_emoji = "🟢"
        lead_text = f"We're leading by {score['gap_stars']:+d}⭐ ({score['gap_destruction']:+.1f}% destruction)"
    elif leading == "them":
        lead_emoji = "🔴"
        lead_text = f"We're behind by {-score['gap_stars']}⭐ ({-score['gap_destruction']:+.1f}% destruction)"
    else:
        lead_emoji = "⚪"
        lead_text = "Tied"
    lines.append(f"{lead_emoji} **{lead_text}**")
    lines.append("")

    if state == "inWar":
        lines.append("## Pending attacks")
        lines.append("")
        if pending:
            for p in pending:
                attacks_owed = p["attacks_owed"]
                lines.append(f"- **{p['name']}** (#{p['map_position']}, TH{p['th']}) — owes {attacks_owed} attack{'s' if attacks_owed != 1 else ''}")
        else:
            lines.append("_All attacks used._")
        lines.append("")
        lines.append("## Projection (if remaining attackers match current avg)")
        lines.append("")
        lines.append(f"- Avg ⭐/attack so far: {proj['avg_stars_per_attack_so_far']}")
        lines.append(f"- Pending attacks: {proj['pending_attacks']}")
        lines.append(f"- Expected final stars: ~{proj['expected_final_stars']}")
        if proj["would_win_at_current_pace"] is True:
            lines.append("- 🟢 **Projection: we win at current pace.**")
        elif proj["would_win_at_current_pace"] is False:
            lines.append("- 🔴 **Projection: we lose at current pace.** Need above-average performance from remaining attackers.")
    elif state == "preparation":
        lines.append("## Roster status")
        lines.append("")
        lines.append(f"- Team size: {len(pending) + len(completed)}")
        lines.append(f"- War starts in: {status['time_remaining_pretty']}")

    return "\n".join(lines)
