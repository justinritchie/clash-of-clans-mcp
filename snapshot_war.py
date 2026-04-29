#!/usr/bin/env python3
"""CLI: snapshot the current war (and CWL rounds, if any) to disk.

Designed to be safe to run repeatedly — idempotent, deduped, prints a clear
status. Can be invoked from a Cowork scheduled task, a cron job, a LaunchAgent,
or by hand.

Usage:
    python snapshot_war.py            # snapshot warEnded states only
    python snapshot_war.py --force    # snapshot in-progress wars too (rare)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coc_mcp.client import CocApiError, CocClient, normalize_tag
from coc_mcp.config import get_api_token, get_default_clan_tag
from coc_mcp.snapshots import (
    list_snapshots,
    reconcile_with_warlog,
    snapshot_cwl_war,
    snapshot_regular_war,
)


async def main(force: bool = False, include_cwl: bool = True) -> int:
    token = get_api_token()
    clan_tag = get_default_clan_tag()
    if not clan_tag:
        print("❌ COC_DEFAULT_CLAN_TAG not set in env.")
        return 1

    client = CocClient(token=token)

    print(f"📦 Snapshotting wars for clan {clan_tag}")
    print()

    # Regular war.
    print("→ Regular war:")
    try:
        war = await client.get_current_war(clan_tag)
        result = snapshot_regular_war(war, force=force)
        symbol = "✅" if result["snapshotted"] else "⏭️ "
        print(f"  {symbol} {result['reason']}")
        if result["snapshotted"]:
            print(f"     {result['path']}")
    except CocApiError as e:
        print(f"  ❌ {e}")
    print()

    # CWL.
    if include_cwl:
        print("→ CWL rounds:")
        try:
            group = await client.get_cwl_group(clan_tag)
            season = group.get("season", "unknown")
            print(f"  Season: {season}")
            normalized_clan = normalize_tag(clan_tag)
            count = 0
            for round_idx, round_obj in enumerate(group.get("rounds", []), start=1):
                for war_tag in round_obj.get("warTags", []):
                    if war_tag in (None, "#0"):
                        continue
                    try:
                        cwl_war = await client.get_cwl_war(war_tag)
                        if cwl_war.get("state") != "warEnded":
                            continue
                        if cwl_war.get("clan", {}).get("tag") != normalized_clan and cwl_war.get("opponent", {}).get("tag") != normalized_clan:
                            continue
                        result = snapshot_cwl_war(cwl_war, season=season, war_tag=war_tag)
                        symbol = "✅" if result["snapshotted"] else "⏭️ "
                        print(f"  {symbol} Round {round_idx} {war_tag}: {result['reason']}")
                        count += 1 if result["snapshotted"] else 0
                    except CocApiError:
                        continue
            if count == 0:
                print("  ⏭️  No new CWL wars to snapshot.")
        except CocApiError:
            print("  ⏭️  Not currently in CWL.")
    print()

    # Reconciliation.
    print("→ Reconciliation vs warlog:")
    try:
        warlog = await client.get_warlog(clan_tag, limit=10)
        recon = reconcile_with_warlog(warlog)
        print(f"  Snapshotted: {recon['snapshotted_count']} / {recon['warlog_total']} (last 10 wars in log)")
        if recon["gaps"]:
            print(f"  ⚠️  {recon['gap_count']} gap(s) — unrecoverable, but flagged for awareness:")
            for gap in recon["gaps"][:5]:
                print(f"     - {gap['end_time']}: vs {gap['opponent_name']} ({gap['result']})")
    except CocApiError as e:
        print(f"  ❌ Could not reconcile: {e}")
    print()

    listing = list_snapshots()
    print(f"📚 Total in store: {listing['regular_war_count']} regular war(s), {listing['cwl_war_count']} CWL war(s)")
    print(f"   {listing['snapshot_dir']}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Snapshot current war(s) to local store.")
    parser.add_argument("--force", action="store_true", help="Snapshot in-progress wars too.")
    parser.add_argument("--no-cwl", action="store_true", help="Skip CWL round snapshots.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(force=args.force, include_cwl=not args.no_cwl)))
