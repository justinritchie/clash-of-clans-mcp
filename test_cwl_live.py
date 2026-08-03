#!/usr/bin/env python3
"""Live smoke test for clash_cwl_lineup / clash_cwl_swap_advice.

Exercises the two map-slot traps against real CWL data:
  1. sparse mapPosition must be re-indexed to true slots 1..N
  2. townhallLevel is absent during `preparation` and must come from leaguegroup

Run: python3 test_cwl_live.py
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")

from coc_mcp_server import (  # noqa: E402
    CwlLineupInput, CwlSwapAdviceInput,
    clash_cwl_lineup, clash_cwl_swap_advice,
)


async def main() -> int:
    print("=== clash_cwl_lineup (latest drawn round) ===")
    raw = await clash_cwl_lineup(CwlLineupInput())
    try:
        d = json.loads(raw)
    except Exception:
        print(raw[:600])
        return 1
    if "error" in d:
        print("  ", d)
        return 1

    print(f"  round {d.get('round')}  state={d.get('state')}  vs {d.get('opponent')}")
    print(f"  war_tag {d.get('war_tag')}  team_size {d.get('team_size')}")
    print(f"  TH sums: ours {d.get('our_th_sum')} vs theirs {d.get('their_th_sum')} "
          f"(delta {d.get('th_sum_delta')})")
    print(f"  negative mirrors at slots: {d.get('negative_mirror_slots')}")
    print()
    raws = [r for r in d["slots"]]
    print("  slot  our player            TH   opp TH  d   rawMapPos")
    for r in raws:
        print(f"  {r['slot']:>4}  {str(r['our_name'])[:20]:<20} {str(r['our_th']):>3}"
              f"   {str(r['their_th']):>5}  {str(r['th_delta']):>3}")

    # TRAP 1: raw mapPosition must be sparse, and slots must be contiguous 1..N
    slots = [r["slot"] for r in raws]
    print()
    print(f"  slots contiguous 1..N: {slots == list(range(1, len(slots) + 1))}")

    # TRAP 2: TH must be populated even in preparation
    missing_th = [r["our_name"] for r in raws if not r["our_th"]]
    print(f"  our TH populated for all slots: {not missing_th}"
          + (f"  MISSING: {missing_th}" if missing_th else ""))

    print()
    print("=== clash_cwl_swap_advice ===")
    raw2 = await clash_cwl_swap_advice(CwlSwapAdviceInput(max_swaps=4))
    a = json.loads(raw2)
    if "error" in a:
        print("  ", a)
        return 1
    print(f"  archive_lag_days: {a.get('archive_lag_days')} "
          f"(latest snapshotted war {a.get('archive_latest_war')})")
    if a.get("STALENESS_WARNING"):
        print(f"  STALENESS_WARNING present: yes")
    print(f"  scored players in archive: {a.get('scored_players')}  bench size: {a.get('bench_size')}")
    print(f"  unevaluated in roster: {a.get('unevaluated_in_roster')}")
    print()
    print("  ranked swaps:")
    for s in a.get("ranked_swaps") or []:
        o, i = s["out"], s["in"]
        print(f"    OUT {str(o['name'])[:18]:<18} (slot {o['slot']}, score {o['score']}, {o['confidence']})"
              f"  <-  IN {str(i['name'])[:18]:<18} (score {i['score']}, {i['confidence']})")
        for r in s["reasons"]:
            print(f"         - {r}")
        if s.get("evidence_warning"):
            print(f"         ! {s['evidence_warning']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
