#!/usr/bin/env python3
"""CLI: run missed-opportunities analysis on the current/most-recent war."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from coc_mcp.client import CocClient
from coc_mcp.config import get_api_token, get_default_clan_tag
from coc_mcp.grading import find_missed_opportunities
from coc_mcp.reporting import missed_opportunities_markdown


async def main() -> int:
    client = CocClient(token=get_api_token())
    war = await client.get_current_war(get_default_clan_tag())
    if war.get("state") in (None, "notInWar"):
        print(f"No war (state={war.get('state')}).")
        return 0
    our = war.get("clan", {}).get("tag")
    missed = find_missed_opportunities(war, our_clan_tag=our, th_buffer=0)
    print(missed_opportunities_markdown(missed))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
