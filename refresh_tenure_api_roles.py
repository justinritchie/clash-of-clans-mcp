#!/usr/bin/env python3
"""Walk the tenure cache and stamp each entry with the live COC API role.

Run this whenever roles change in-game. The COS-derived `current_role` field
in the cache can be stale or misordered; `api_current_role` is the source of
truth for "is this player currently a Member / Elder / Co-leader / Leader?"

Usage:
    python refresh_tenure_api_roles.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coc_mcp.client import CocApiError, CocClient
from coc_mcp.config import get_api_token, get_default_clan_tag
from coc_mcp.tenure import list_cached_tenure, update_api_role


# COC API uses different role names than the in-game UI:
#   leader → Leader
#   coLeader → Co-leader
#   admin → Elder
#   member → Member
ROLE_DISPLAY = {
    "leader": "Leader",
    "coLeader": "Co-leader",
    "admin": "Elder",
    "member": "Member",
}


async def main() -> int:
    token = get_api_token()
    clan_tag = get_default_clan_tag()
    client = CocClient(token=token)

    cached = list_cached_tenure()
    print(f"📚 {len(cached)} cached tenure entries found.")
    print()

    # Pull current clan members (one API call) — gives us authoritative roles for everyone in clan.
    try:
        members_data = await client.get_clan_members(clan_tag)
    except CocApiError as e:
        print(f"❌ Could not fetch clan members: {e}")
        return 1
    members_by_tag = {m["tag"].upper(): m for m in (members_data.get("items") or [])}

    updated = 0
    skipped_not_in_clan = 0
    role_changed = []

    for entry in cached:
        tag = entry["tag"].upper()
        member = members_by_tag.get(tag)
        if not member:
            print(f"⏭️  {entry.get('name', '?'):20s} ({tag}): not currently in clan; skipping")
            skipped_not_in_clan += 1
            continue
        api_role_raw = member.get("role", "member")
        api_role = ROLE_DISPLAY.get(api_role_raw, api_role_raw)
        old_cos_role = entry.get("current_role")
        if old_cos_role and old_cos_role.lower().replace("-", "") != api_role.lower().replace("-", ""):
            role_changed.append((entry.get("name"), old_cos_role, api_role))
        update_api_role(entry["tag"], api_role)
        updated += 1
        marker = "✅"
        flag = " ⚠️ disagrees with COS" if old_cos_role and old_cos_role != api_role else ""
        print(f"{marker} {entry.get('name', '?'):20s} ({tag}): {api_role}{flag}")

    print()
    print(f"Updated: {updated}")
    print(f"Skipped (not in clan anymore): {skipped_not_in_clan}")
    if role_changed:
        print()
        print(f"⚠️  {len(role_changed)} entries had COS role disagreeing with API role:")
        for name, cos_role, api_role in role_changed:
            print(f"   - {name}: COS said {cos_role}, API says {api_role}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
