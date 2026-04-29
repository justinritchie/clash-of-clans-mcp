"""Configurable war attack grading engine.

The rubric is loaded from JSON (config/rubric.default.json) and can be overridden
per-call. The engine is intentionally generic so new rules can be added by editing
the JSON without touching this code.

Vocabulary
----------
- mapPosition: 1-indexed position on the war map (1 = strongest, N = weakest).
- mirror: defender at the same mapPosition as attacker.
- offset: defender_pos - attacker_pos. 0 = mirror, +N = N positions weaker.
- regular war: 2 attacks per player.
- CWL war: 1 attack per player per round.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


# --- Helpers ---------------------------------------------------------------

def _index_members_by_tag(members: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {m["tag"]: m for m in members}


def _attack_score(stars: int, scoring: Dict[str, int]) -> int:
    """Map stars -> base score using the scoring table."""
    return {
        3: scoring.get("three_star", 10),
        2: scoring.get("two_star", 5),
        1: scoring.get("one_star", 2),
        0: scoring.get("zero_star", 0),
    }.get(stars, 0)


def _flatten_war_attacks(war: Dict[str, Any], our_clan_tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """Build a flat list of attack records for *our* side of a war.

    If our_clan_tag is provided, picks the matching side. Otherwise assumes 'clan' is us.
    Each record:
        {
          attacker_tag, attacker_name, attacker_pos,
          defender_tag, defender_pos, offset,
          stars, destruction, order  (order = 1st or 2nd attack by this player)
        }
    """
    if our_clan_tag and war.get("opponent", {}).get("tag") == our_clan_tag:
        our_side = war["opponent"]
        their_side = war["clan"]
    else:
        our_side = war.get("clan", {})
        their_side = war.get("opponent", {})

    our_members = our_side.get("members", []) or []
    their_members = their_side.get("members", []) or []
    their_pos = {m["tag"]: m["mapPosition"] for m in their_members}

    records: List[Dict[str, Any]] = []
    for member in our_members:
        attacks = member.get("attacks", []) or []
        # Sort by attack order if present, else preserve insertion.
        attacks_sorted = sorted(attacks, key=lambda a: a.get("order", 0))
        for idx, atk in enumerate(attacks_sorted, start=1):
            defender_tag = atk.get("defenderTag")
            defender_pos = their_pos.get(defender_tag)
            attacker_pos = member.get("mapPosition")
            offset = (defender_pos - attacker_pos) if (defender_pos is not None and attacker_pos is not None) else None
            records.append({
                "attacker_tag": member["tag"],
                "attacker_name": member.get("name"),
                "attacker_pos": attacker_pos,
                "attacker_th": member.get("townhallLevel"),
                "defender_tag": defender_tag,
                "defender_pos": defender_pos,
                "offset": offset,
                "stars": atk.get("stars", 0),
                "destruction": atk.get("destructionPercentage", 0.0),
                "attack_seq": idx,  # 1st or 2nd attack by this player
                "order": atk.get("order"),  # global order in war
            })
    return records


def _missed_attacks_per_player(war: Dict[str, Any], attacks_per_player: int, our_clan_tag: Optional[str] = None) -> Dict[str, int]:
    """For each member, how many of their attacks are missing."""
    if our_clan_tag and war.get("opponent", {}).get("tag") == our_clan_tag:
        our_members = war["opponent"].get("members", []) or []
    else:
        our_members = war.get("clan", {}).get("members", []) or []

    out: Dict[str, int] = {}
    for m in our_members:
        used = len(m.get("attacks") or [])
        out[m["tag"]] = max(0, attacks_per_player - used)
    return out


def find_missed_opportunities(
    war: Dict[str, Any],
    *,
    our_clan_tag: Optional[str] = None,
    th_buffer: int = 0,
    first_attack_offsets: Tuple[int, ...] = (0, 1),
    war_type: str = "regular",
) -> List[Dict[str, Any]]:
    """Identify attacks where a smarter target was actually available at the time.

    Respects the standard attack rules:
      - First attack: hit mirror or one-down (offsets 0 or +1 by default).
        If the player followed this rule, NOT a missed opportunity even if they
        didn't 3-star (they went after their assigned target).
        ONLY flag a first attack if it broke the rule (reached up, or skipped
        too far past one-down) AND a correct target was available.
      - Second attack: smart 3-star rule. If didn't 3-star AND a weaker
        undefeated base (≤ attacker_th + buffer) was sitting there, flag it.

    Args:
        war: Raw war JSON.
        our_clan_tag: Our clan tag. Auto-detect if absent.
        th_buffer: Allow weaker bases up to this many TH levels above the
            attacker's TH (default 0 = strictly weaker or equal TH).
        first_attack_offsets: Acceptable offsets for first attack
            (default (0, 1) = mirror or one-down).
        war_type: 'regular' (2 attacks per player) or 'cwl' (1 attack — treated
            like a first attack for rule compliance).

    Returns:
        List of missed-opportunity records sorted by severity.
    """
    # Identify our side and their side.
    if our_clan_tag and war.get("opponent", {}).get("tag") == our_clan_tag:
        our_side, their_side = war["opponent"], war["clan"]
    else:
        our_side, their_side = war.get("clan", {}), war.get("opponent", {})

    our_members = our_side.get("members", []) or []
    their_members = their_side.get("members", []) or []

    # Index opponents by tag for quick lookup; track their position + TH.
    opp_by_tag = {m["tag"]: m for m in their_members}
    opp_tags_by_pos = {m["mapPosition"]: m["tag"] for m in their_members}

    # Build a flat, chronological list of OUR attacks.
    flat: List[Dict[str, Any]] = []
    for m in our_members:
        attacks = m.get("attacks", []) or []
        attacks_sorted = sorted(attacks, key=lambda a: a.get("order", 0))
        for seq, atk in enumerate(attacks_sorted, start=1):
            flat.append({
                "attacker_member": m,
                "attack": atk,
                "seq": seq,
            })
    flat.sort(key=lambda r: r["attack"].get("order", 0))

    # Track defender bases that are 3-starred (cleared) at any point.
    cleared_positions: set = set()
    out: List[Dict[str, Any]] = []

    for entry in flat:
        m = entry["attacker_member"]
        atk = entry["attack"]
        seq = entry["seq"]

        attacker_pos = m.get("mapPosition")
        attacker_th = m.get("townhallLevel", 0)
        defender = opp_by_tag.get(atk.get("defenderTag"), {})
        defender_pos = defender.get("mapPosition")
        defender_th = defender.get("townhallLevel", 0)
        stars = atk.get("stars", 0)
        destruction = atk.get("destructionPercentage", 0.0)

        # Find weaker undefeated bases AT THIS MOMENT.
        # "Weaker" = higher map position (positions are strongest-first).
        weaker_avail: List[Dict[str, Any]] = []
        if defender_pos is not None:
            for opp_pos, opp_tag in sorted(opp_tags_by_pos.items()):
                if opp_pos <= defender_pos:
                    continue  # not weaker than what they actually attacked
                if opp_pos in cleared_positions:
                    continue  # already 3-starred, not "available"
                opp = opp_by_tag.get(opp_tag, {})
                opp_th = opp.get("townhallLevel", 0)
                # Only flag as missed opportunity if attacker could likely handle it
                # (defender TH ≤ attacker TH + buffer).
                if opp_th <= attacker_th + th_buffer:
                    weaker_avail.append({
                        "pos": opp_pos,
                        "tag": opp_tag,
                        "name": opp.get("name"),
                        "th": opp_th,
                    })

        # Decide if this attack qualifies as a "missed opportunity" by sequence.
        offset = defender_pos - attacker_pos if (defender_pos and attacker_pos) else 0
        is_missed = False
        violation_kind = ""

        if seq == 1 or war_type == "cwl":
            # First attack (or CWL single attack): rule is mirror/one-down.
            # If the player followed the rule, NOT a missed opportunity — they
            # went after their assigned target, even if they didn't 3-star.
            # Only flag if they BROKE the rule AND a correct target was available.
            if offset not in first_attack_offsets:
                # Did they reach up (negative offset) or skip past one-down (>1)?
                # Either way, was a "correct" target (mirror or one-down position)
                # actually undefeated at the time?
                correct_positions = [attacker_pos + o for o in first_attack_offsets if attacker_pos]
                correct_undefeated = [
                    p for p in correct_positions
                    if p in opp_tags_by_pos and p not in cleared_positions
                ]
                if correct_undefeated and stars < 3:
                    is_missed = True
                    violation_kind = "broke first-attack rule"
        else:
            # Second attack: smart 3-star rule. If didn't 3-star AND a weaker
            # undefeated base was sitting there, missed opportunity.
            if stars < 3 and len(weaker_avail) > 0:
                is_missed = True
                violation_kind = "second-attack should have gone for the easier 3⭐"

        if is_missed:
            # Severity: how much was left on the table?
            weakest_avail_pos = max(w["pos"] for w in weaker_avail) if weaker_avail else None
            gap = (weakest_avail_pos - defender_pos) if weakest_avail_pos is not None else 0
            if offset < 0 and stars <= 1:
                severity = "high"
            elif gap >= 3 or stars == 0:
                severity = "high"
            elif gap >= 1 and stars <= 1:
                severity = "medium"
            else:
                severity = "low"

            note = (
                f"Attack #{seq}: {violation_kind}. "
                f"Hit base #{defender_pos} (TH{defender_th}) for {stars}⭐ {destruction:.0f}%; "
                f"{len(weaker_avail)} weaker undefeated base(s) available."
            )

            out.append({
                "attacker_name": m.get("name"),
                "attacker_tag": m["tag"],
                "attacker_pos": attacker_pos,
                "attacker_th": attacker_th,
                "attack_order": atk.get("order"),
                "attack_seq": seq,
                "violation_kind": violation_kind,
                "actual_target_pos": defender_pos,
                "actual_target_th": defender_th,
                "actual_stars": stars,
                "actual_destruction": destruction,
                "available_weaker_undefeated": weaker_avail,
                "severity": severity,
                "note": note,
            })

        # Update cleared set AFTER evaluating this attack (it might 3-star).
        if stars >= 3 and defender_pos is not None:
            cleared_positions.add(defender_pos)

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: (severity_rank[r["severity"]], -(r["actual_target_pos"] or 0)))
    return out


# --- Public API ------------------------------------------------------------

def grade_war(
    war: Dict[str, Any],
    rubric: Dict[str, Any],
    *,
    war_type: str = "regular",
    our_clan_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply rubric to a war's attack data and return per-player grades.

    Args:
        war: Raw war JSON from /clans/{tag}/currentwar or /clanwarleagues/wars/{tag}.
        rubric: Loaded rubric dict.
        war_type: 'regular' (2 attacks) or 'cwl' (1 attack).
        our_clan_tag: Our clan tag. Auto-detected if absent (assumes 'clan' field is us).

    Returns:
        {
          'war_type': str,
          'players': [
            {
              'tag': str, 'name': str, 'map_position': int, 'th': int,
              'attacks_used': int, 'attacks_missed': int,
              'attack_records': [...],   # flattened attack details
              'rule_violations': [str],  # human-readable
              'score': int,
              'grade': 'A'..'F',
              'notes': [str],
            }
          ],
          'summary': { total_attacks, missed, avg_stars, avg_destruction },
          'rubric_used': {war_type, ...},
        }
    """
    if war_type == "cwl":
        section = rubric["cwl_attack_rubric"]
    else:
        section = rubric["war_attack_rubric"]

    attacks_per_player = section["attacks_per_player"]
    scoring = section["scoring"]
    missed_penalty = section["missed_attack_penalty"]

    # Flatten attacks and group by attacker.
    records = _flatten_war_attacks(war, our_clan_tag=our_clan_tag)
    by_attacker: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_attacker.setdefault(r["attacker_tag"], []).append(r)

    # Build per-player member info from war.
    if our_clan_tag and war.get("opponent", {}).get("tag") == our_clan_tag:
        our_members = war["opponent"].get("members", []) or []
    else:
        our_members = war.get("clan", {}).get("members", []) or []
    member_by_tag = _index_members_by_tag(our_members)

    missed_map = _missed_attacks_per_player(war, attacks_per_player, our_clan_tag=our_clan_tag)

    players_out: List[Dict[str, Any]] = []
    total_attacks = 0
    total_stars = 0
    total_destruction = 0.0
    total_missed = 0

    for tag, member in member_by_tag.items():
        atks = sorted(by_attacker.get(tag, []), key=lambda r: r["attack_seq"])
        missed = missed_map.get(tag, 0)
        total_missed += missed

        violations: List[str] = []
        notes: List[str] = []
        score = 0

        for atk in atks:
            score += _attack_score(atk["stars"], scoring)
            total_attacks += 1
            total_stars += atk["stars"]
            total_destruction += atk["destruction"]

        # Apply per-attack rules.
        if war_type == "cwl":
            atk_rules = section["attack"]
            # CWL: only one attack expected.
            if atks:
                atk = atks[0]
                if atk["offset"] is not None and atk["offset"] not in atk_rules["acceptable_offsets"]:
                    violations.append(
                        f"CWL attack hit base #{atk['defender_pos']} (offset {atk['offset']:+d}); "
                        f"acceptable offsets are {atk_rules['acceptable_offsets']}."
                    )
                if atk_rules.get("must_3_star") and atk["stars"] < 3:
                    violations.append(f"CWL attack scored {atk['stars']}⭐ (rubric requires 3⭐).")
                elif atk["stars"] < atk_rules["min_stars_for_pass"]:
                    violations.append(
                        f"CWL attack scored {atk['stars']}⭐ "
                        f"(rubric minimum {atk_rules['min_stars_for_pass']}⭐)."
                    )
        else:
            # Regular war: first + second attacks.
            first_rules = section["first_attack"]
            second_rules = section["second_attack"]

            if atks:
                first = atks[0]
                if first["offset"] is not None and first["offset"] not in first_rules["acceptable_offsets"]:
                    violations.append(
                        f"1st attack hit base #{first['defender_pos']} (offset {first['offset']:+d}); "
                        f"rule = mirror or one down ({first_rules['acceptable_offsets']})."
                    )
                    score += scoring.get("first_attack_off_target_penalty", 0)
                if first["stars"] < first_rules["min_stars_for_pass"]:
                    violations.append(
                        f"1st attack scored only {first['stars']}⭐ ({first['destruction']:.0f}%); "
                        f"rubric minimum {first_rules['min_stars_for_pass']}⭐."
                    )

            if len(atks) >= 2:
                second = atks[1]
                ok_offset = (
                    second["offset"] is not None
                    and second_rules["preferred_offsets_min"]
                    <= second["offset"]
                    <= second_rules["preferred_offsets_max"]
                )
                if not ok_offset and second["offset"] is not None:
                    violations.append(
                        f"2nd attack target offset {second['offset']:+d} outside preferred "
                        f"[{second_rules['preferred_offsets_min']}, {second_rules['preferred_offsets_max']}] "
                        "(should typically be a smart 3-star pick lower than your rank)."
                    )
                if second_rules.get("must_3_star"):
                    if second["stars"] >= 3:
                        score += scoring.get("second_attack_3_star_bonus", 0)
                    elif second["destruction"] < second_rules.get("min_destruction_if_no_3_star", 100):
                        violations.append(
                            f"2nd attack didn't 3⭐ and only hit {second['destruction']:.0f}% "
                            f"(rubric demands 3⭐ or ≥{second_rules.get('min_destruction_if_no_3_star')}%)."
                        )

        if missed:
            score += missed_penalty * missed
            violations.append(f"Missed {missed} attack(s) (penalty {missed_penalty * missed}).")

        grade = _score_to_letter(score)
        if not atks and missed == 0:
            notes.append("Member did not attack but no attacks were owed (perhaps just joined CWL roster).")

        players_out.append({
            "tag": tag,
            "name": member.get("name"),
            "map_position": member.get("mapPosition"),
            "th": member.get("townhallLevel"),
            "attacks_used": len(atks),
            "attacks_missed": missed,
            "attack_records": atks,
            "rule_violations": violations,
            "score": score,
            "grade": grade,
            "notes": notes,
        })

    avg_stars = (total_stars / total_attacks) if total_attacks else 0.0
    avg_destruction = (total_destruction / total_attacks) if total_attacks else 0.0

    # Sort by map_position (war order) for predictable output.
    players_out.sort(key=lambda p: p.get("map_position") or 999)

    return {
        "war_type": war_type,
        "players": players_out,
        "summary": {
            "total_attacks_used": total_attacks,
            "total_attacks_missed": total_missed,
            "avg_stars_per_attack": round(avg_stars, 2),
            "avg_destruction_per_attack": round(avg_destruction, 2),
        },
        "rubric_used": section,
    }


def _score_to_letter(score: int) -> str:
    if score >= 18:
        return "A"
    if score >= 12:
        return "B"
    if score >= 6:
        return "C"
    if score >= 0:
        return "D"
    return "F"


def aggregate_player_war_history(
    graded_wars: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Aggregate a list of graded wars into a per-player summary across wars.

    Returns:
        { player_tag: { name, wars_played, attacks_used, attacks_missed,
                        avg_stars, avg_destruction, total_score, grades: [...] } }
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for war in graded_wars:
        for p in war["players"]:
            tag = p["tag"]
            if tag not in agg:
                agg[tag] = {
                    "tag": tag,
                    "name": p["name"],
                    "wars_played": 0,
                    "attacks_used": 0,
                    "attacks_missed": 0,
                    "stars": 0,
                    "destruction_sum": 0.0,
                    "attack_count": 0,
                    "total_score": 0,
                    "grades": [],
                    "violations": 0,
                }
            row = agg[tag]
            row["wars_played"] += 1
            row["attacks_used"] += p["attacks_used"]
            row["attacks_missed"] += p["attacks_missed"]
            for atk in p["attack_records"]:
                row["stars"] += atk["stars"]
                row["destruction_sum"] += atk["destruction"]
                row["attack_count"] += 1
            row["total_score"] += p["score"]
            row["grades"].append(p["grade"])
            row["violations"] += len(p["rule_violations"])

    # Compute averages.
    for row in agg.values():
        n = row["attack_count"]
        row["avg_stars"] = round(row["stars"] / n, 2) if n else 0.0
        row["avg_destruction"] = round(row["destruction_sum"] / n, 2) if n else 0.0
    return agg


def carry_forward_recommendation(
    aggregated: Dict[str, Dict[str, Any]],
    rubric: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply carry_forward_rubric to aggregated stats. Returns sorted list.

    Each item: { tag, name, recommendation: 'keep'|'bench'|'review', reasons: [str], stats: {...} }
    """
    cfg = rubric["carry_forward_rubric"]
    out: List[Dict[str, Any]] = []
    for row in aggregated.values():
        attacks_owed = row["attacks_used"] + row["attacks_missed"]
        used_pct = (row["attacks_used"] / attacks_owed * 100) if attacks_owed else 0.0

        reasons: List[str] = []
        rec = "keep"

        if used_pct < cfg["min_attacks_used_pct"]:
            rec = "bench"
            reasons.append(f"Attack participation {used_pct:.0f}% (min {cfg['min_attacks_used_pct']}%).")
        if row["avg_stars"] < cfg["min_avg_stars"]:
            if rec == "keep":
                rec = "review"
            reasons.append(f"Avg stars {row['avg_stars']} (min {cfg['min_avg_stars']}).")
        if row["avg_destruction"] < cfg["min_avg_destruction"]:
            if rec == "keep":
                rec = "review"
            reasons.append(f"Avg destruction {row['avg_destruction']:.0f}% (min {cfg['min_avg_destruction']}%).")

        if not reasons:
            reasons.append("Meets all carry-forward criteria.")

        out.append({
            "tag": row["tag"],
            "name": row["name"],
            "recommendation": rec,
            "reasons": reasons,
            "stats": {
                "wars_played": row["wars_played"],
                "attacks_used": row["attacks_used"],
                "attacks_missed": row["attacks_missed"],
                "attack_participation_pct": round(used_pct, 1),
                "avg_stars": row["avg_stars"],
                "avg_destruction": row["avg_destruction"],
                "total_score": row["total_score"],
                "violations": row["violations"],
            },
        })

    # Sort: keeps first (by score desc), then reviews, then benches.
    rec_order = {"keep": 0, "review": 1, "bench": 2}
    out.sort(key=lambda r: (rec_order[r["recommendation"]], -r["stats"]["total_score"]))
    return out


def promotion_candidates(
    members: List[Dict[str, Any]],
    aggregated_war: Dict[str, Dict[str, Any]],
    rubric: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply elder_promotion_rubric to current members + war history.

    Args:
        members: Roster from /clans/{tag}/members. Each item has tag, name, role, donations,
                 donationsReceived, etc.
        aggregated_war: Output of aggregate_player_war_history (may be empty).
        rubric: Loaded rubric.

    Returns:
        Sorted list of candidates with reasons.
    """
    cfg = rubric["elder_promotion_rubric"]
    out: List[Dict[str, Any]] = []

    for m in members:
        if m.get("role") in ("elder", "coLeader", "leader", "admin"):
            continue  # already at or above elder rank
        donated = m.get("donations", 0) or 0
        received = m.get("donationsReceived", 0) or 0
        ratio = (donated / received) if received else (1.0 if donated else 0.0)

        war_row = aggregated_war.get(m["tag"], {})
        war_score = war_row.get("total_score", 0)

        reasons: List[str] = []
        eligible = True

        if ratio < cfg["min_donation_ratio"]:
            eligible = False
            reasons.append(f"Donation ratio {ratio:.2f} below {cfg['min_donation_ratio']}.")
        if war_score < cfg["min_war_score_last_n"]:
            eligible = False
            reasons.append(
                f"War score {war_score} over last {war_row.get('wars_played', 0)} war(s) "
                f"below threshold {cfg['min_war_score_last_n']}."
            )
        missed = war_row.get("attacks_missed", 0)
        if missed > cfg["max_missed_attacks_window"]:
            eligible = False
            reasons.append(f"Missed {missed} attacks in window (max {cfg['max_missed_attacks_window']}).")

        if eligible and not reasons:
            reasons.append("Meets all elder criteria.")

        out.append({
            "tag": m["tag"],
            "name": m.get("name"),
            "role": m.get("role"),
            "th": m.get("townHallLevel"),
            "trophies": m.get("trophies"),
            "donation_ratio": round(ratio, 2),
            "donations": donated,
            "received": received,
            "war_score_window": war_score,
            "missed_attacks_window": missed,
            "eligible": eligible,
            "reasons": reasons,
        })

    out.sort(key=lambda r: (not r["eligible"], -r["war_score_window"], -r["donation_ratio"]))
    return out
