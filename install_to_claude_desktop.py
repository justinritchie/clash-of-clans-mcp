#!/usr/bin/env python3
"""Add the coc MCP server entry to Claude Desktop's config.

Safe: makes a backup, preserves all other MCP entries, idempotent.
"""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
PROJECT_DIR = Path(__file__).resolve().parent
SERVER_PATH = PROJECT_DIR / "coc_mcp_server.py"
ENV_FILE = PROJECT_DIR / ".env"


def load_env_token() -> str:
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE}. Create it from .env.example first.")
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("COC_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("COC_API_TOKEN not found in .env")


def load_env_clan_tag() -> str:
    if not ENV_FILE.exists():
        return "#YV9JRULU"
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("COC_DEFAULT_CLAN_TAG="):
            return line.split("=", 1)[1].strip()
    return "#YV9JRULU"


def main() -> int:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Claude Desktop config not found at {CONFIG_PATH}")

    if not SERVER_PATH.exists():
        raise SystemExit(f"Server file not found at {SERVER_PATH}")

    token = load_env_token()
    clan_tag = load_env_clan_tag()

    # Backup
    backup = CONFIG_PATH.with_suffix(f".json.bak.{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(CONFIG_PATH, backup)
    print(f"📦 Backup written to: {backup}")

    # Load + edit
    with CONFIG_PATH.open() as f:
        cfg = json.load(f)

    cfg.setdefault("mcpServers", {})
    existed = "coc" in cfg["mcpServers"]
    cfg["mcpServers"]["coc"] = {
        "command": "python3",
        "args": [str(SERVER_PATH)],
        "env": {
            "COC_API_TOKEN": token,
            "COC_DEFAULT_CLAN_TAG": clan_tag,
        },
    }

    # Write back, preserving 2-space indent that Claude Desktop seems to use.
    with CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    action = "Updated" if existed else "Added"
    print(f"✅ {action} 'coc' MCP entry in {CONFIG_PATH}")
    print(f"   command: python3 {SERVER_PATH}")
    print(f"   default clan: {clan_tag}")
    print()
    print("Restart Claude Desktop for the new server to appear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
