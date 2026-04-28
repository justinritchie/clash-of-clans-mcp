#!/usr/bin/env python3
"""Quick test of the raw_get passthrough against endpoints not wrapped by named tools."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from coc_mcp.client import CocClient
from coc_mcp.config import get_api_token


async def main() -> int:
    c = CocClient(token=get_api_token())

    print("--- Test 1: /goldpass/seasons/current (not wrapped) ---")
    gp = await c.raw_get("/goldpass/seasons/current")
    print(f"Gold pass: {gp.get('startTime')} -> {gp.get('endTime')}")

    print("\n--- Test 2: /clans/%23YV9JRULU/capitalraidseasons (not wrapped) ---")
    raids = await c.raw_get("/clans/%23YV9JRULU/capitalraidseasons", params={"limit": 1})
    items = raids.get("items", [])
    if items:
        r = items[0]
        print(f"Latest raid: {r.get('startTime')} -> {r.get('endTime')}")
        print(f"  Capital loot: {r.get('capitalTotalLoot')}")
        print(f"  Defensive: {r.get('defensiveReward')}")

    print("\n--- Test 3: /locations (not wrapped) ---")
    locs = await c.raw_get("/locations", params={"limit": 3})
    for loc in locs.get("items", [])[:3]:
        print(f"  {loc.get('id')}: {loc.get('name')}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
