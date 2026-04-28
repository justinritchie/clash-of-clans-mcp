#!/usr/bin/env python3
"""Smoke test for the COC MCP server.

Hits a few read-only endpoints with your token and prints a one-line OK/FAIL per check.
Run from this directory: `python coc_test.py`.

Requires:
  - .env file with COC_API_TOKEN (and optionally COC_DEFAULT_CLAN_TAG)
  - Your current IP must match the token's whitelisted IP.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make package importable when run from project root.
sys.path.insert(0, str(Path(__file__).parent))

from coc_mcp.client import CocApiError, CocClient
from coc_mcp.config import get_api_token, get_default_clan_tag


CHECKMARK = "✅"
CROSSMARK = "❌"


async def check(label: str, coro):
    try:
        result = await coro
        print(f"{CHECKMARK} {label}")
        return result
    except CocApiError as e:
        print(f"{CROSSMARK} {label}  → HTTP {e.status}: {e}")
    except Exception as e:
        print(f"{CROSSMARK} {label}  → {type(e).__name__}: {e}")
    return None


async def main() -> int:
    try:
        token = get_api_token()
    except RuntimeError as e:
        print(f"{CROSSMARK} {e}")
        return 1

    clan_tag = get_default_clan_tag() or "#YV9JRULU"
    print(f"Using clan: {clan_tag}")
    print(f"Token prefix: {token[:24]}…\n")

    client = CocClient(token=token)

    clan = await check("GET /clans/<tag>", client.get_clan(clan_tag))
    if clan:
        print(f"   Clan: {clan.get('name')} (level {clan.get('clanLevel')}) — "
              f"{clan.get('members')} members, {clan.get('warWins')}W")

    members = await check("GET /clans/<tag>/members", client.get_clan_members(clan_tag))
    if members:
        items = members.get("items", [])
        print(f"   {len(items)} members listed; top 3 by donations:")
        top = sorted(items, key=lambda m: -m.get("donations", 0))[:3]
        for m in top:
            print(f"     - {m['name']}: donated {m.get('donations', 0)}, received {m.get('donationsReceived', 0)}")

    warlog = await check("GET /clans/<tag>/warlog", client.get_warlog(clan_tag, limit=3))
    if warlog:
        items = warlog.get("items", [])
        print(f"   Last 3 wars:")
        for w in items:
            print(f"     - vs {w.get('opponent', {}).get('name', '?')}: {w.get('result')}")

    cwl = await check("GET /clans/<tag>/currentwar/leaguegroup", client.get_cwl_group(clan_tag))
    if cwl:
        rounds = cwl.get("rounds", [])
        print(f"   CWL group state: {cwl.get('state')} — {len(rounds)} rounds, {len(cwl.get('clans', []))} clans")

    war = await check("GET /clans/<tag>/currentwar", client.get_current_war(clan_tag))
    if war:
        print(f"   Regular war state: {war.get('state')}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
