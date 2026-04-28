"""Markdown report generators for human-readable war summaries."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def missed_opportunities_markdown(missed: List[Dict[str, Any]]) -> str:
    """Render a list of missed-opportunity records as a markdown section."""
    if not missed:
        return "## ⭐ Missed Opportunities (lower base was available + undefeated)\n\n_None — every reach-up either succeeded or had no easier target available._\n"

    by_severity = {"high": [], "medium": [], "low": []}
    for r in missed:
        by_severity[r["severity"]].append(r)

    lines = ["## ⭐ Missed Opportunities (lower base was available + undefeated)", ""]
    lines.append(
        f"_{len(missed)} attack(s) reached for a target when an easier, undefeated base was sitting right there._"
    )
    lines.append("")
    for sev_label, sev_emoji in (("high", "🔴 High"), ("medium", "🟡 Medium"), ("low", "⚪ Low")):
        items = by_severity[sev_label]
        if not items:
            continue
        lines.append(f"### {sev_emoji} severity ({len(items)})")
        lines.append("")
        for r in items:
            avail_summary = ", ".join(
                f"#{w['pos']} (TH{w['th']})" for w in r["available_weaker_undefeated"][:5]
            )
            extra = ""
            if len(r["available_weaker_undefeated"]) > 5:
                extra = f", +{len(r['available_weaker_undefeated']) - 5} more"
            lines.append(
                f"- **{r['attacker_name']}** (#{r['attacker_pos']}, TH{r['attacker_th']}, attack #{r['attack_seq']}) → "
                f"hit #{r['actual_target_pos']} (TH{r['actual_target_th']}) for **{r['actual_stars']}⭐ {r['actual_destruction']:.0f}%**. "
                f"Easier targets undefeated at the time: {avail_summary}{extra}."
            )
        lines.append("")
    return "\n".join(lines)


def war_report_markdown(graded: Dict[str, Any], war_meta: Dict[str, Any] | None = None, missed: Optional[List[Dict[str, Any]]] = None) -> str:
    """Render a graded war as a leadership-friendly markdown post-mortem.

    Sections answer the four common leadership questions:
      1. Who did both attacks (and who didn't)
      2. Rule compliance (mirror+1, smart 2nd)
      3. Performance leaderboard
      4. Smart-attack honor roll
    """
    players = graded["players"]
    summary = graded["summary"]
    war_type = graded["war_type"]

    lines: List[str] = []

    # Header
    if war_meta:
        opp = war_meta.get("opponent_name", "Unknown")
        result = war_meta.get("result", "")
        lines.append(f"# War Report — vs {opp}{f' ({result})' if result else ''}")
    else:
        lines.append(f"# War Report ({war_type})")
    lines.append("")
    lines.append(
        f"Total attacks used: **{summary['total_attacks_used']}** · "
        f"Missed: **{summary['total_attacks_missed']}** · "
        f"Avg ⭐/attack: **{summary['avg_stars_per_attack']}** · "
        f"Avg destruction: **{summary['avg_destruction_per_attack']:.0f}%**"
    )
    lines.append("")

    # 1. Who did both attacks
    expected = 1 if war_type == "cwl" else 2
    full_participants = [p for p in players if p["attacks_used"] >= expected]
    partial = [p for p in players if 0 < p["attacks_used"] < expected]
    no_show = [p for p in players if p["attacks_used"] == 0]

    lines.append(f"## 1. Attack Participation ({expected}/{expected} expected)")
    lines.append("")
    if war_type == "cwl":
        lines.append(f"- **Used their attack ({len(full_participants)})**: " + _names(full_participants))
    else:
        lines.append(f"- **Used both attacks ({len(full_participants)})**: " + _names(full_participants))
        if partial:
            lines.append(f"- **Used only 1 of 2 ({len(partial)})**: " + _names(partial))
    if no_show:
        lines.append(f"- **🚨 Did not attack ({len(no_show)})**: " + _names(no_show))
    lines.append("")

    # 2. Rule compliance
    lines.append("## 2. Rule Compliance")
    lines.append("")
    clean = [p for p in players if not p["rule_violations"] and p["attacks_used"] > 0]
    dirty = [p for p in players if p["rule_violations"]]
    lines.append(f"- **Clean ({len(clean)})**: " + (_names(clean) if clean else "_(none)_"))
    if dirty:
        lines.append(f"- **Violations ({len(dirty)})**:")
        for p in dirty:
            lines.append(f"  - **{p['name']}** (#{p['map_position']}, TH{p['th']}):")
            for v in p["rule_violations"]:
                lines.append(f"    - {v}")
    lines.append("")

    # 3. Performance leaderboard
    lines.append("## 3. Performance Leaderboard")
    lines.append("")
    lines.append("| Rank | Name | Pos | TH | ⭐ Earned | Avg Dest % | Score | Grade |")
    lines.append("|---|---|---|---|---|---|---|---|")
    perf_sorted = sorted(
        players,
        key=lambda p: (
            -sum(a["stars"] for a in p["attack_records"]),
            -sum(a["destruction"] for a in p["attack_records"]),
        ),
    )
    for i, p in enumerate(perf_sorted, start=1):
        atks = p["attack_records"]
        stars_total = sum(a["stars"] for a in atks)
        dest_avg = (sum(a["destruction"] for a in atks) / len(atks)) if atks else 0
        lines.append(
            f"| {i} | {p['name']} | #{p['map_position']} | TH{p['th'] or '?'} | "
            f"{stars_total} | {dest_avg:.0f}% | {p['score']} | {p['grade']} |"
        )
    lines.append("")

    # 4. Smart-attack honor roll (only meaningful for regular war)
    if war_type != "cwl":
        smart_picks = []
        for p in players:
            atks = p["attack_records"]
            if len(atks) >= 2:
                second = atks[1]
                if second["stars"] >= 3 and second["offset"] is not None and second["offset"] >= 1:
                    smart_picks.append((p, second))
        lines.append("## 4. Smart-Attack Honor Roll (clean 3⭐ on lower base)")
        lines.append("")
        if smart_picks:
            for p, atk in smart_picks:
                lines.append(
                    f"- **{p['name']}** (#{p['map_position']}) → "
                    f"hit base #{atk['defender_pos']} (offset +{atk['offset']}) for 3⭐ {atk['destruction']:.0f}%"
                )
        else:
            lines.append("_No qualifying smart 3⭐ attacks this war._")
        lines.append("")

    # 5. Missed opportunities (cross-cutting analysis using attack order)
    if missed is not None:
        lines.append(missed_opportunities_markdown(missed))

    return "\n".join(lines)


def _names(players: List[Dict[str, Any]]) -> str:
    if not players:
        return "_(none)_"
    return ", ".join(f"{p['name']} (#{p['map_position']})" for p in players)


def carry_forward_markdown(recommendations: List[Dict[str, Any]]) -> str:
    """Render carry-forward recommendations as markdown."""
    keep = [r for r in recommendations if r["recommendation"] == "keep"]
    review = [r for r in recommendations if r["recommendation"] == "review"]
    bench = [r for r in recommendations if r["recommendation"] == "bench"]

    lines: List[str] = ["# CWL Carry-Forward Recommendation", ""]
    lines.append(f"**Keep**: {len(keep)} · **Review**: {len(review)} · **Bench**: {len(bench)}")
    lines.append("")

    for label, group in (("✅ Keep", keep), ("⚠️ Review", review), ("🚫 Bench", bench)):
        lines.append(f"## {label} ({len(group)})")
        lines.append("")
        if not group:
            lines.append("_(none)_")
            lines.append("")
            continue
        lines.append("| Name | Wars | Atk Used | Avg ⭐ | Avg Dest % | Score | Reasons |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in group:
            s = r["stats"]
            lines.append(
                f"| {r['name']} | {s['wars_played']} | "
                f"{s['attacks_used']}/{s['attacks_used']+s['attacks_missed']} ({s['attack_participation_pct']}%) | "
                f"{s['avg_stars']} | {s['avg_destruction']:.0f}% | {s['total_score']} | "
                f"{'; '.join(r['reasons'])} |"
            )
        lines.append("")

    return "\n".join(lines)
