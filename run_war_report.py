#!/usr/bin/env python3
"""Quick CLI runner for the war report. Run after coc_test.py is green.

Usage:
    python run_war_report.py            # current/most-recent regular war
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coc_mcp.client import CocClient
from coc_mcp.config import get_api_token, get_default_clan_tag, load_rubric
from coc_mcp.grading import grade_war
from coc_mcp.reporting import war_report_markdown


async def main() -> int:
    token = get_api_token()
    clan_tag = get_default_clan_tag()
    rubric = load_rubric()
    client = CocClient(token=token)

    war = await client.get_current_war(clan_tag)
    state = war.get("state", "?")
    if state in ("notInWar", None):
        print(f"No war to report on (state={state}).")
        return 0

    our_clan_tag = war.get("clan", {}).get("tag")
    graded = grade_war(war, rubric, war_type="regular", our_clan_tag=our_clan_tag)

    opp = war.get("opponent", {}).get("name", "Opponent")
    our = war.get("clan", {})
    them = war.get("opponent", {})
    if our.get("stars", 0) > them.get("stars", 0):
        result = "Victory"
    elif our.get("stars", 0) < them.get("stars", 0):
        result = "Defeat"
    else:
        result = "Tie"

    print(war_report_markdown(graded, war_meta={"opponent_name": opp, "result": f"{result} — state={state}"}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
