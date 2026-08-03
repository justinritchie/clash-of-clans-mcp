#!/usr/bin/env python3
"""Recommend a war lineup (10v10 vs 15v15) from recency-weighted performance.

Design premise (Justin, 2026-07-26): players drift in and out of engagement, so
ALL-TIME AVERAGES ARE NEARLY WORTHLESS for picking a lineup. What matters is
"who showed up and performed in the most recent war," then the last week, then
the last month. All-time is a faint tiebreak only.

Recency buckets and weights:
    most recent war   0.45
    last 7 days       0.30
    last 30 days      0.18
    all time          0.07

Per-war player score blends attendance and effectiveness:
    war_score = 0.55 * attendance + 0.45 * (avg_stars / 3.0)

Attendance is weighted highest because the failure mode that actually loses
15v15 wars is unused attacks, not low-star attacks.

Confidence is reported per player so a recommendation built on one stale war is
never mistaken for one built on real evidence.

Usage:
    python3 recommend_lineup.py             # human-readable report
    python3 recommend_lineup.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
SNAPSHOTS = REPO / "snapshots"
OPTIN_FILE = REPO / "war_optins.txt"

W_RECENT, W_WEEK, W_MONTH, W_ALL = 0.45, 0.30, 0.18, 0.07
W_ATTENDANCE, W_STARS = 0.55, 0.45

# A player must clear this blended score to be considered lineup-worthy.
QUALIFY_BAR = 0.55


# --------------------------------------------------------------------------
# env / api
# --------------------------------------------------------------------------
def load_env() -> tuple[str, str]:
    text = (REPO / ".env").read_text()
    token = re.search(r"^COC_API_TOKEN=(.*)$", text, re.M).group(1).strip()
    clan = re.search(r"^COC_DEFAULT_CLAN_TAG=(.*)$", text, re.M).group(1).strip()
    return token, clan


def api(path: str, token: str):
    req = urllib.request.Request(
        "https://api.clashofclans.com/v1" + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s.split(".")[0], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# snapshots -> per-player per-war records
# --------------------------------------------------------------------------
def load_war_files() -> list[Path]:
    files = []
    if (SNAPSHOTS / "wars").is_dir():
        files += sorted((SNAPSHOTS / "wars").glob("*.json"))
    if (SNAPSHOTS / "cwl").is_dir():
        files += sorted(SNAPSHOTS.glob("cwl/*/*.json"))
    return files


def extract_records(clan_tag: str) -> list[dict]:
    """One record per (player, war): attendance + avg stars."""
    records = []
    norm = clan_tag.upper().replace("#", "")
    for f in load_war_files():
        try:
            war = json.loads(f.read_text())
        except Exception:
            continue
        end = war.get("endTime")
        if not end:
            continue
        try:
            end_dt = parse_ts(end)
        except Exception:
            continue

        # Our side may be under .clan or .opponent depending on the matchup.
        side = None
        for key in ("clan", "opponent"):
            blob = war.get(key) or {}
            if (blob.get("tag") or "").upper().replace("#", "") == norm:
                side = blob
                break
        if side is None:
            side = war.get("clan") or {}

        # CWL snapshots omit attacksPerMember but allow exactly ONE attack.
        # Defaulting those to 2 makes every CWL participant look like a 50%
        # no-show, which silently tanks the whole roster's attendance.
        is_cwl = "cwl" in f.parts or war.get("warStartTime") is not None
        per_member = war.get("attacksPerMember") or (1 if is_cwl else 2)
        for m in side.get("members") or []:
            attacks = m.get("attacks") or []
            used = len(attacks)
            stars = [a.get("stars", 0) for a in attacks]
            records.append(
                {
                    "tag": m.get("tag", "").upper(),
                    "name": m.get("name"),
                    "th": m.get("townhallLevel"),
                    "end": end_dt,
                    "attendance": min(used / per_member, 1.0) if per_member else 0.0,
                    "avg_stars": (sum(stars) / len(stars)) if stars else 0.0,
                    "used": used,
                    "owed": per_member,
                }
            )
    return records


def bucket_score(recs: list[dict]) -> float | None:
    if not recs:
        return None
    att = sum(r["attendance"] for r in recs) / len(recs)
    stars = sum(r["avg_stars"] for r in recs) / len(recs)
    return W_ATTENDANCE * att + W_STARS * (stars / 3.0)


def score_players(records: list[dict], now: datetime) -> dict[str, dict]:
    by_tag: dict[str, list[dict]] = {}
    for r in records:
        by_tag.setdefault(r["tag"], []).append(r)

    latest_war_end = max((r["end"] for r in records), default=None)
    all_ends = {r["end"] for r in records}

    # A bucket is ACTIVE only if the clan actually fought a war in that window.
    # This matters: if the clan hasn't warred in 7 days, nobody should be
    # punished for an empty 7-day bucket. But if wars DID happen and a player
    # sat them out, that absence must count against them as a zero — otherwise
    # a player who drifted away keeps coasting on stale all-time numbers.
    week_active = any(e >= now - timedelta(days=7) for e in all_ends)
    month_active = any(e >= now - timedelta(days=30) for e in all_ends)

    out = {}
    for tag, recs in by_tag.items():
        recs.sort(key=lambda r: r["end"], reverse=True)
        most_recent = [r for r in recs if latest_war_end and r["end"] == latest_war_end]
        week = [r for r in recs if r["end"] >= now - timedelta(days=7)]
        month = [r for r in recs if r["end"] >= now - timedelta(days=30)]

        # NOT SELECTED is not the same as NO-SHOWED.
        #
        # extract_records() walks each war's ROSTER, so a player who WAS picked
        # and didn't attack already yields a record with attendance 0 — a real
        # negative signal, correctly scored zero by bucket_score().
        #
        # A player with NO record for a war simply wasn't picked. Scoring that
        # as zero charges them 45% for a decision the operator made. In CWL it
        # compounds daily: 7 rounds, 15 slots from ~45 members, so each round
        # the ~30 benched players take another 45% hit, and by round 3-4 the
        # ranking measures "who did I pick recently" — feeding the operator his
        # own prior choices back as if they were evidence.
        #
        # So absent-from-roster => bucket INACTIVE for that player (weight
        # dropped, denominator renormalised). Rostered-and-idle still scores
        # zero. That distinction is the entire fix.
        buckets = [
            (most_recent, W_RECENT, latest_war_end is not None and bool(most_recent)),
            (week, W_WEEK, week_active and bool(week)),
            (month, W_MONTH, month_active and bool(month)),
            (recs, W_ALL, True),
        ]
        total, denom = 0.0, 0.0
        for bucket, w, active in buckets:
            if not active:
                continue          # window had no wars, or player wasn't rostered
            denom += w
            s = bucket_score(bucket)
            total += (s * w) if s is not None else 0.0
        score = (total / denom) if denom else 0.0

        # Confidence reflects how much RECENT evidence exists, not total volume.
        if most_recent and len(month) >= 3:
            conf = "high"
        elif most_recent or len(month) >= 2:
            conf = "med"
        else:
            conf = "low"

        out[tag] = {
            "tag": tag,
            "name": recs[0]["name"],
            "th": recs[0]["th"],
            "score": round(score, 3),
            "confidence": conf,
            "wars_total": len(recs),
            "wars_30d": len(month),
            "in_most_recent": bool(most_recent),
            "last_seen": recs[0]["end"].strftime("%Y-%m-%d"),
            "attendance_30d": round(sum(r["attendance"] for r in month) / len(month), 2) if month else None,
            "avg_stars_30d": round(sum(r["avg_stars"] for r in month) / len(month), 2) if month else None,
        }
    return out


# --------------------------------------------------------------------------
# format recommendation from warlog
# --------------------------------------------------------------------------
def format_history(token: str, clan: str) -> dict:
    log = api(f"/clans/{clan.replace('#', '%23')}/warlog?limit=50", token)
    items = [w for w in log.get("items", []) if w.get("attacksPerMember") == 2 and w.get("result")]
    out = {}
    for size in (10, 15):
        rows = [w for w in items if w.get("teamSize") == size]
        if not rows:
            continue
        recent = rows[:6]

        def margin(rs):
            return sum(r["clan"]["stars"] - r["opponent"]["stars"] for r in rs) / len(rs) / size

        out[size] = {
            "n": len(rows),
            "W": sum(1 for r in rows if r["result"] == "win"),
            "L": sum(1 for r in rows if r["result"] == "lose"),
            "T": sum(1 for r in rows if r["result"] == "tie"),
            "margin_per_slot_all": round(margin(rows), 3),
            "margin_per_slot_recent": round(margin(recent), 3) if recent else None,
            "n_recent": len(recent),
        }
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    token, clan = load_env()
    now = datetime.now(timezone.utc)

    records = extract_records(clan)
    scores = score_players(records, now)

    # Live roster: anyone not currently in the clan cannot be fielded.
    members = api(f"/clans/{clan.replace('#', '%23')}/members", token).get("items", [])
    in_clan = {m["tag"].upper(): m for m in members}

    # Opt-ins (optional file, one tag or name per line, # comments allowed).
    optins = set()
    if OPTIN_FILE.exists():
        for line in OPTIN_FILE.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                optins.add(line.upper())

    roster = []
    for tag, m in in_clan.items():
        s = scores.get(tag)
        roster.append(
            {
                "tag": tag,
                "name": m.get("name"),
                "th": m.get("townHallLevel"),
                "role": m.get("role"),
                "score": s["score"] if s else 0.0,
                "confidence": s["confidence"] if s else "none",
                "wars_30d": s["wars_30d"] if s else 0,
                "attendance_30d": s["attendance_30d"] if s else None,
                "avg_stars_30d": s["avg_stars_30d"] if s else None,
                "last_seen": s["last_seen"] if s else None,
                "opted_in": tag in optins or (m.get("name", "").upper() in optins),
                "donations": m.get("donations", 0),
            }
        )

    # Opt-in players float to the top of their score tier; TH breaks ties when
    # we have no performance signal at all.
    roster.sort(key=lambda r: (r["opted_in"], r["score"], r["th"] or 0), reverse=True)

    qualified = [r for r in roster if r["score"] >= QUALIFY_BAR]
    fmt = format_history(token, clan)

    if len(qualified) >= 15:
        rec, why = 15, f"{len(qualified)} players clear the quality bar — enough depth to fill 15 slots."
    elif len(qualified) >= 10:
        rec, why = 10, f"only {len(qualified)} players clear the bar; slots 11-15 would be filled by unproven players."
    else:
        rec, why = 10, f"just {len(qualified)} players clear the bar — thin roster, 10v10 limits exposure."

    result = {
        "generated_at": now.isoformat(),
        "recommended_size": rec,
        "rationale": why,
        "qualified_count": len(qualified),
        "format_history": fmt,
        "lineup": roster[:rec],
        "next_in_line": roster[rec : rec + 5],
        "data_depth": {
            "wars_in_archive": len(load_war_files()),
            "players_with_any_history": len(scores),
            "players_with_30d_history": sum(1 for s in scores.values() if s["wars_30d"] > 0),
        },
        "weights": {"most_recent_war": W_RECENT, "last_7d": W_WEEK, "last_30d": W_MONTH, "all_time": W_ALL},
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    d = result["data_depth"]
    print(f"RECOMMENDED: {rec}v{rec}  — {why}")
    print(f"Archive depth: {d['wars_in_archive']} wars | {d['players_with_30d_history']} players with last-30d data")
    print()
    print("Format history (regular wars):")
    for size, f in sorted(fmt.items()):
        print(
            f"  {size}v{size}: {f['W']}-{f['L']}-{f['T']} over {f['n']} wars | "
            f"star margin/slot all={f['margin_per_slot_all']:+} recent{f['n_recent']}={f['margin_per_slot_recent']:+}"
        )
    print()
    print(f"{'#':<3}{'NAME':<20}{'TH':<4}{'SCORE':<7}{'CONF':<6}{'30d':<5}{'ATT':<6}{'STARS':<6}{'OPT':<5}LAST")
    for i, p in enumerate(roster[:rec], 1):
        print(
            f"{i:<3}{(p['name'] or '?')[:19]:<20}{p['th'] or '-':<4}{p['score']:<7}{p['confidence']:<6}"
            f"{p['wars_30d']:<5}{str(p['attendance_30d'] or '-'):<6}{str(p['avg_stars_30d'] or '-'):<6}"
            f"{'yes' if p['opted_in'] else '':<5}{p['last_seen'] or 'never'}"
        )
    print()
    print("Next in line:")
    for p in result["next_in_line"]:
        print(f"   {(p['name'] or '?')[:19]:<20} score={p['score']:<7}conf={p['confidence']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
