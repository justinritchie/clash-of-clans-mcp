# Clash of Clans MCP Server

An MCP server for clan leadership: wraps the official [Clash of Clans API](https://developer.clashofclans.com) and adds workflow tools for war grading, carry-forward recommendations, and elder-promotion screening.

Built around a **configurable rubric** (JSON) so your clan rules can change without touching code.

## What it does

**Read-only API wrappers**:

- `clash_get_clan` — clan-level details
- `clash_get_clan_members` — full roster with roles/donations/trophies
- `clash_get_warlog` — past wars (summary only — see limitation below)
- `clash_get_current_war` — current regular war with full attack data
- `clash_get_cwl_group` — current CWL group + war tags per round
- `clash_get_cwl_war` — individual CWL war by tag
- `clash_get_player` — player profile

**Workflow tools (the leverage)**:

- `clash_grade_war` — apply the rubric to current war's attacks; returns per-player grades, scores, rule violations
- `clash_war_report` — markdown post-mortem answering: who used both attacks, who followed the rules, performance leaderboard, smart-attack honor roll
- `clash_carry_forward_recommendation` — analyzes all CWL rounds and recommends keep / review / bench for next season
- `clash_promotion_candidates` — screens current Members against your elder bar (donation ratio, hero progress, war contribution)

## Limitations

- **Per-attack data only available for the *current* war.** The official `/warlog` endpoint returns summary data only (result, totals). To analyze past wars, snapshot `/currentwar` to disk while state is `warEnded`. (Future enhancement: a `clash_snapshot_war` tool + local store.)
- **No clan chat access.** Supercell intentionally doesn't expose chat in the API. Use a Discord bridge bot for chat-based workflows.
- **API token is IP-whitelisted.** If your IP changes, regenerate the token at developer.clashofclans.com or proxy through a fixed-IP host.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get an API token

Visit [developer.clashofclans.com](https://developer.clashofclans.com), log in with your Supercell ID, click **Create New Key**, and whitelist your current IP (`curl ifconfig.me`).

### 3. Configure environment

Copy `.env.example` to `.env` and fill in your token:

```bash
cp .env.example .env
# edit .env
```

### 4. Smoke test

```bash
python coc_test.py
```

You should see `✅` next to each endpoint check. If you see `❌ HTTP 403`, your IP doesn't match the token's whitelisted IP.

### 5. Run the unit tests

```bash
pip install pytest
pytest tests/ -v
```

These run against fixture JSON, no API needed.

### 6. Add to Claude Desktop / Cowork

Copy `mcp_config_example.json` into your `claude_desktop_config.json` (or Cowork's MCP config), updating the path:

```json
{
  "mcpServers": {
    "coc": {
      "command": "python3",
      "args": ["/path/to/coc-mcp/coc_mcp_server.py"],
      "env": {
        "COC_API_TOKEN": "eyJ0eXAi...",
        "COC_DEFAULT_CLAN_TAG": "#YOURCLAN"
      }
    }
  }
}
```

Restart your client. The `coc_*` tools should appear.

## Customizing the rubric

The default rubric is `config/rubric.default.json`. The grading rules — what's a "smart" attack, what's the elder bar — are all there and editable.

Per-call overrides work too: pass `rubric_overrides` to `clash_grade_war` or `clash_war_report` with just the fields you want to change. Deep-merged onto the loaded rubric.

Example: temporarily relax the smart-attack rule for a CWL grading run:

```json
{
  "war_attack_rubric": {
    "second_attack": {
      "must_3_star": false
    }
  }
}
```

## Project layout

```
coc-mcp/
├── coc_mcp_server.py       # FastMCP entry — tool registrations
├── coc_mcp/
│   ├── client.py           # Async COC API client
│   ├── grading.py          # Rubric engine
│   ├── reporting.py        # Markdown generators
│   └── config.py           # Token + rubric loading
├── config/
│   └── rubric.default.json # Default rubric (edit freely)
├── tests/
│   ├── fixtures/           # Sample war JSON for tests
│   └── test_grading.py     # Unit tests
├── coc_test.py             # Live smoke-test script
└── mcp_config_example.json # Drop into Claude Desktop config
```

## License

MIT — see `LICENSE`.

## Acknowledgements

This product uses data from the Clash of Clans API but is not affiliated with, endorsed, sponsored, or specifically approved by Supercell. For more information see Supercell's Fan Content Policy: [supercell.com/fan-content-policy](https://www.supercell.com/fan-content-policy).
