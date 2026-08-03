"""CWL round reading and lineup swap advice.

The operator question this exists to answer, once per round:

    Given who is locked into today's roster and how they actually performed,
    who do I swap out and who do I bring in?

The constraint that shapes it: clan members do not reliably announce going
inactive. Every input must be derived from observed behaviour — did they use
their attack, how many stars — never from asking. The tool has to be the thing
that notices.

TWO TRAPS, both found on live data. A naive implementation hits both:

1. `mapPosition` is a SPARSE ROSTER INDEX, not the war-map slot. A live round-1
   roster returned positions 1,2,3,5,8,9,10,11,14,15,16,17,18,25,30 for fifteen
   players. Mirrors are only correct after sorting each side by mapPosition and
   RE-INDEXING 1..N. Comparing raw mapPosition across sides silently produces
   wrong matchups — wrong in a way that looks entirely plausible.

2. Town hall level is ABSENT from war member objects during `preparation`. It
   has to be joined from the leaguegroup roster. Note the casing difference:
   war members use `townhallLevel` (lowercase h) once populated; leaguegroup
   uses `townHallLevel`. Reading only one spelling yields zeros.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _norm(tag: Optional[str]) -> str:
    return (tag or "").upper().replace("#", "").strip()


def th_of(member: Dict[str, Any]) -> Optional[int]:
    """Town hall level, tolerating both spellings. See trap 2."""
    for key in ("townhallLevel", "townHallLevel"):
        v = member.get(key)
        if isinstance(v, int) and v > 0:
            return v
    return None


def build_th_index(league_group: Dict[str, Any]) -> Dict[str, int]:
    """tag -> TH from the leaguegroup roster.

    During `preparation` the war objects carry no TH at all, so this is the
    only source. Built once and joined onto both sides.
    """
    idx: Dict[str, int] = {}
    for clan in (league_group or {}).get("clans") or []:
        for m in clan.get("members") or []:
            th = th_of(m)
            if th:
                idx[_norm(m.get("tag"))] = th
    return idx


def true_slots(side: Dict[str, Any], th_index: Dict[str, int]) -> List[Dict[str, Any]]:
    """Members at TRUE map slots 1..N. See trap 1 — this is the re-index."""
    members = list(side.get("members") or [])
    members.sort(key=lambda m: m.get("mapPosition") or 9999)
    out = []
    for i, m in enumerate(members, start=1):
        tag = _norm(m.get("tag"))
        out.append(
            {
                "slot": i,
                "raw_map_position": m.get("mapPosition"),
                "tag": tag,
                "name": m.get("name"),
                "th": th_of(m) or th_index.get(tag),
                "attacks_used": len(m.get("attacks") or []),
                "stars": sum(a.get("stars", 0) for a in (m.get("attacks") or [])),
            }
        )
    return out


def resolve_round_war(
    league_group: Dict[str, Any], clan_tag: str, round_no: Optional[int]
) -> Tuple[Optional[str], Optional[int], List[str]]:
    """(war_tag, round_number, notes) for our clan's war in a round.

    round_no is 1-indexed. None means "the latest round that has real war tags",
    which is the round in progress or most recently started.
    """
    notes: List[str] = []
    ours = _norm(clan_tag)
    rounds = (league_group or {}).get("rounds") or []
    if not rounds:
        return None, None, ["leaguegroup returned no rounds"]

    # '#0' is CoC's placeholder for a round that has not been drawn yet.
    playable = [
        (i + 1, [t for t in (r.get("warTags") or []) if t and t != "#0"])
        for i, r in enumerate(rounds)
    ]
    playable = [(n, tags) for n, tags in playable if tags]
    if not playable:
        return None, None, ["no rounds have war tags yet — group still forming"]

    if round_no is None:
        round_no = playable[-1][0]
        notes.append(f"round not specified — using latest drawn round {round_no}")

    match = next((tags for n, tags in playable if n == round_no), None)
    if match is None:
        return None, round_no, [f"round {round_no} has no war tags yet"]
    return None, round_no, notes + [f"__TAGS__{','.join(match)}"]


def lineup_from_war(
    war: Dict[str, Any], clan_tag: str, th_index: Dict[str, int]
) -> Optional[Dict[str, Any]]:
    """Our 15 vs theirs at true slots, with per-slot TH deltas."""
    ours = _norm(clan_tag)
    side_us, side_them = None, None
    for key, other in (("clan", "opponent"), ("opponent", "clan")):
        blob = war.get(key) or {}
        if _norm(blob.get("tag")) == ours:
            side_us, side_them = blob, war.get(other) or {}
            break
    if side_us is None:
        return None

    us = true_slots(side_us, th_index)
    them = true_slots(side_them, th_index)

    rows = []
    for i in range(max(len(us), len(them))):
        a = us[i] if i < len(us) else None
        b = them[i] if i < len(them) else None
        delta = None
        if a and b and a["th"] and b["th"]:
            delta = a["th"] - b["th"]
        rows.append(
            {
                "slot": i + 1,
                "our_name": a["name"] if a else None,
                "our_tag": a["tag"] if a else None,
                "our_th": a["th"] if a else None,
                "our_attacks_used": a["attacks_used"] if a else None,
                "our_stars": a["stars"] if a else None,
                "their_name": b["name"] if b else None,
                "their_th": b["th"] if b else None,
                "th_delta": delta,
            }
        )

    our_th_sum = sum(r["our_th"] or 0 for r in rows)
    their_th_sum = sum(r["their_th"] or 0 for r in rows)
    return {
        "state": war.get("state"),
        "team_size": war.get("teamSize"),
        "our_clan": side_us.get("name"),
        "opponent": side_them.get("name"),
        "start_time": war.get("startTime"),
        "end_time": war.get("endTime"),
        "slots": rows,
        "our_th_sum": our_th_sum,
        "their_th_sum": their_th_sum,
        "th_sum_delta": our_th_sum - their_th_sum,
        "negative_mirror_slots": [r["slot"] for r in rows if (r["th_delta"] or 0) < 0],
        "roster_tags": [r["our_tag"] for r in rows if r["our_tag"]],
    }


def swap_advice(
    lineup: Dict[str, Any],
    scores: Dict[str, Dict[str, Any]],
    clan_members: List[Dict[str, Any]],
    cwl_no_shows: Dict[str, int],
    max_swaps: int = 5,
    war_prefs: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Ranked OUT <- IN pairs, each carrying its evidence inline.

    Swap-out priority, in order:
      1. missed a CWL attack THIS season — one attack is a seventh of a
         player's entire CWL contribution, and an unexplained no-show is the
         strongest available proxy for someone having gone quiet
      2. low score sitting on a NEGATIVE TH mirror — a weak attacker parked on
         a slot they are already outmatched on is the compounding worst case
      3. low score generally
      4. never appeared in a snapshotted war — flagged UNEVALUATED, explicitly
         not "bad"

    Evidence travels with each row rather than in a footnote: these get read
    fast on a phone during a prep window.
    """
    # Tag formats diverge across this codebase: extract_records() keeps the
    # leading '#', the war/leaguegroup payloads carry it, and _norm() strips it.
    # A mismatch here does not error — every lookup just misses, and the whole
    # roster silently reports as "unevaluated", which reads as a data problem
    # rather than a key-format bug. Normalise once, at the boundary.
    scores = {_norm(k): v for k, v in (scores or {}).items()}

    # In-game war preference ("in"/"out") is the player's own stated intent —
    # the one input here that IS explicit communication rather than inferred
    # behaviour. It cuts both ways and both directions matter:
    #   - opted OUT and on the bench  -> never recommend them in
    #   - opted OUT and IN the roster -> strongest possible swap-OUT signal,
    #                                    they have said they do not want to war
    prefs = {_norm(k): (v or "").lower() for k, v in (war_prefs or {}).items()}

    rostered = {r["our_tag"] for r in lineup["slots"] if r["our_tag"]}

    def row_for(tag: str, name: Optional[str], th: Optional[int]) -> Dict[str, Any]:
        s = scores.get(tag)
        if s is None:
            return {
                "tag": tag, "name": name, "th": th,
                "score": None, "confidence": "none",
                "note": "UNEVALUATED — never appeared in a snapshotted war. "
                        "Not evidence of poor play; evidence of a gap in the archive.",
            }
        return {
            "tag": tag, "name": s.get("name") or name, "th": s.get("th") or th,
            "score": s.get("score"), "confidence": s.get("confidence"),
            "wars_30d": s.get("wars_30d"), "last_seen": s.get("last_seen"),
        }

    review = []
    for r in lineup["slots"]:
        if not r["our_tag"]:
            continue
        row = row_for(r["our_tag"], r["our_name"], r["our_th"])
        row.update(
            {
                "slot": r["slot"],
                "th_delta": r["th_delta"],
                "attacks_used_this_round": r["our_attacks_used"],
                "cwl_no_shows_this_season": cwl_no_shows.get(r["our_tag"], 0),
                "war_preference": prefs.get(r["our_tag"]),
            }
        )
        review.append(row)

    bench = []
    excluded_opted_out = []
    for m in clan_members:
        tag = _norm(m.get("tag"))
        if tag in rostered:
            continue
        row = row_for(tag, m.get("name"), m.get("townHallLevel") or m.get("townhallLevel"))
        row["cwl_no_shows_this_season"] = cwl_no_shows.get(tag, 0)
        row["war_preference"] = prefs.get(tag)
        # Opted out is a stated choice, not a performance signal. Never
        # recommend bringing someone in who has asked to be left out —
        # regardless of how well they score.
        if row["war_preference"] == "out":
            excluded_opted_out.append({"name": row["name"], "tag": tag, "score": row.get("score")})
            continue
        bench.append(row)

    # Penalty applied to a scored player sitting on a slot they are already
    # outmatched on. It is a MODIFIER, not a separate tier: the priority is
    # "low score AT a negative mirror", so a strong player on a -1 must not
    # outrank a genuinely weak player on an even mirror. An earlier version
    # tiered on the mirror alone and pushed a 0.73 ahead of a 0.24 — which is
    # exactly backwards, and only visible by checking against a known-good
    # worked example rather than by reading the code.
    NEG_MIRROR_PENALTY = 0.15

    def out_priority(row: Dict[str, Any]) -> tuple:
        no_show = row.get("cwl_no_shows_this_season", 0)
        score = row.get("score")
        neg = (row.get("th_delta") or 0) < 0
        if score is None:
            effective = 1.5          # unevaluated ranks AFTER anything scored
        else:
            effective = score - (NEG_MIRROR_PENALTY if neg else 0.0)
        # Someone rostered who has set war preference to OUT outranks every
        # other signal: they have explicitly said they do not want to war, so
        # leaving them in wastes a slot no matter how well they score.
        opted_out = row.get("war_preference") == "out"
        # Lower sorts first = higher swap-out priority.
        return (0 if opted_out else 1, 0 if no_show >= 1 else 1, effective)

    def in_priority(row: Dict[str, Any]) -> tuple:
        return (
            1 if row.get("cwl_no_shows_this_season", 0) >= 1 else 0,
            -(row.get("score") if row.get("score") is not None else -1),
        )

    out_ranked = sorted(review, key=out_priority)
    in_ranked = sorted(bench, key=in_priority)

    swaps = []
    used_in = set()
    for o in out_ranked[:max_swaps]:
        cand = next(
            (
                c for c in in_ranked
                if c["tag"] not in used_in
                and c.get("score") is not None
                and (o.get("score") is None or c["score"] > o["score"])
            ),
            None,
        )
        if not cand:
            continue
        used_in.add(cand["tag"])
        reasons = []
        if o.get("war_preference") == "out":
            reasons.append(
                "HAS OPTED OUT of war in-game — they have said they do not want "
                "to be in; leaving them rostered wastes a slot"
            )
        if o.get("cwl_no_shows_this_season", 0) >= 1:
            reasons.append(
                f"missed {o['cwl_no_shows_this_season']} CWL attack(s) this season — "
                "strongest available signal that they have gone quiet"
            )
        if (o.get("th_delta") or 0) < 0:
            reasons.append(f"already outmatched at slot {o.get('slot')} (TH delta {o['th_delta']})")
        if o.get("score") is None:
            reasons.append("no record in the archive — unevaluated, not judged poor")
        elif o["score"] < 0.5:
            reasons.append(f"low score {o['score']}")
        mirror_cost = None
        if o.get("th") and cand.get("th"):
            mirror_cost = cand["th"] - o["th"]
            reasons.append(
                "no mirror cost — same TH" if mirror_cost == 0
                else f"mirror change {mirror_cost:+d} TH at slot {o.get('slot')}"
            )
        conf_warn = None
        if cand.get("confidence") in ("low", "none") or o.get("confidence") in ("low", "none"):
            conf_warn = (
                "PROPOSED ON WEAK EVIDENCE — "
                f"out={o.get('confidence')}, in={cand.get('confidence')}"
            )
        swaps.append(
            {
                "out": {k: o.get(k) for k in ("name", "tag", "slot", "score", "confidence", "th", "th_delta", "cwl_no_shows_this_season")},
                "in": {k: cand.get(k) for k in ("name", "tag", "score", "confidence", "th")},
                "mirror_cost_th": mirror_cost,
                "reasons": reasons,
                "evidence_warning": conf_warn,
            }
        )

    return {
        "roster_review": sorted(review, key=lambda r: r["slot"]),
        "eligible_bench": in_ranked[:15],
        "ranked_swaps": swaps,
        "bench_size": len(bench),
        "unevaluated_in_roster": [r["name"] for r in review if r.get("confidence") == "none"],
        "excluded_opted_out": excluded_opted_out,
        "opted_out_in_roster": [
            r["name"] for r in review if r.get("war_preference") == "out"
        ],
        "war_preference_checked": bool(prefs),
    }
