# Clash of Clans MCP Server

A Model Context Protocol (MCP) server that lets Claude (or any MCP client) read your clan's data from the **official Clash of Clans API** and apply your clan's own rules to it — for war grading, elder promotion screening, and CWL carry-forward decisions.

Built for clan leaders who want **AI-assisted analysis**, not another Discord bot.

---

## What you can do with it

Once connected to Claude Desktop (or another MCP client), you can ask things like:

> "How did our last war go? Who didn't follow the rules?"

> "Should I promote ankit176 to Elder? What's our policy bar?"

> "Pull together a CWL carry-forward recommendation for next season."

> "Who in the clan has the highest donation ratio this season?"

> "Pull our capital raid loot for the last 5 weekends."

The MCP turns each question into the right API calls, applies your **configurable rubric** (in JSON, edit freely), and returns a leadership-ready answer.

---

## Sample output

After you run it on a finished war, you get something like:

```markdown
# War Report — vs clash serious (Defeat)

Total attacks used: 26 · Missed: 4 · Avg ⭐/attack: 2.27 · Avg destruction: 83%

## 1. Attack Participation (2/2 expected)
- Used both attacks (12): Justin (#1), Slayer7 (#2), TURBO (#3), ...
- Used only 1 of 2 (2): M@NJ!T (#4), Laeo_Lol XD (#11)
- 🚨 Did not attack (1): Punjabi Pete (#15)

## 2. Rule Compliance
- Clean (3): Justin (#1), aNkiT (#6), joe (#10)
- Violations (12):
  - Slayer7 (#2, TH18): 2nd attack didn't 3⭐ and only hit 80%
  - M@NJ!T (#4, TH18): 1st attack hit base #10 (offset +6); rule = mirror or one down
  - ankit176 (#8, TH17): 2nd attack target offset -3 (should hit lower bases for smart 3⭐)
  - ...

## 3. Performance Leaderboard
| Rank | Name | Pos | TH | ⭐ Earned | Avg Dest % | Score | Grade |
| 1 | Justin | #1 | TH18 | 6 | 100% | 23 | A |
| 2 | Corrupt | #5 | TH17 | 6 | 100% | 20 | A |
...

## 4. Smart-Attack Honor Roll (clean 3⭐ on lower base)
- Justin (#1) → hit base #2 for 3⭐ 100%
- Corrupt (#5) → hit base #9 for 3⭐ 100%
```

---

## Tools the MCP exposes

**API wrappers (read-only):**

| Tool | Purpose |
|---|---|
| `clash_get_clan` | Clan-level details (name, level, war league, members count) |
| `clash_get_clan_members` | Roster with role, donations, war stars, trophies |
| `clash_get_warlog` | Past wars (summary only — see limitations) |
| `clash_get_current_war` | Current regular war with **full per-attack data** |
| `clash_get_cwl_group` | Current CWL group + war tags per round |
| `clash_get_cwl_war` | Individual CWL war by tag |
| `clash_get_player` | Player profile (heroes, troops, war stars, current clan) |
| `clash_api_get` | **Generic GET passthrough** for any endpoint not wrapped above (gold pass, capital raids, leagues, locations, clan search, etc.) |

**Workflow tools (the leverage):**

| Tool | Purpose |
|---|---|
| `clash_grade_war` | Apply your rubric to a war's attacks → per-player grades, scores, rule violations |
| `clash_war_report` | Markdown post-mortem answering: who attacked, who followed rules, performance leaderboard, smart-attack honor roll |
| `clash_carry_forward_recommendation` | Analyze all CWL rounds → keep / review / bench list for next season |
| `clash_promotion_candidates` | Screen Members against your elder bar (donation ratio, hero progress, war contribution) |

---

## Getting started

### 1. Get a free Clash of Clans API token

This MCP wraps the **official Supercell API**, which is free but requires a token tied to your IP address.

1. Open [developer.clashofclans.com](https://developer.clashofclans.com).
2. Click **Login** and sign in with your **Supercell ID** (the same one tied to your in-game account — you'll get an email code).
3. Once logged in, go to **My Account** → **Create New Key**.
4. Find your current public IP — easiest:
   ```bash
   curl ifconfig.me
   ```
5. Fill in the form:
   - **Key Name**: anything (e.g. `coc-mcp`)
   - **Description**: anything (e.g. `MCP server for clan analysis`)
   - **Allowed IP Addresses**: paste the IP you got from `curl ifconfig.me`
6. Click **Create Key**. Copy the long **JWT token** that appears (starts with `eyJ...`).

   > 🛡️ **The token is IP-locked**. If your IP changes (new ISP, VPN, coffee shop), regenerate the key with the new IP, OR run the MCP from a host with a fixed IP (a small VPS, a home server, etc.).

### 2. Find your clan tag

In-game, tap your clan name → look for the tag like `#YV9JRULU`. Note: **clan tags use uppercase letters and digits only** (no `O`, only `0`).

### 3. Clone and install

```bash
git clone https://github.com/<your-username>/coc-mcp.git
cd coc-mcp
pip install -r requirements.txt
```

### 4. Configure your token

```bash
cp .env.example .env
```

Edit `.env`:

```bash
COC_API_TOKEN=eyJ0eXAiOiJKV1Q...    # <-- paste your token
COC_DEFAULT_CLAN_TAG=#YV9JRULU       # <-- your clan tag
```

The `.env` file is `.gitignore`d, so your token won't accidentally get committed.

### 5. Smoke test

```bash
python coc_test.py
```

You should see green checkmarks for every endpoint:

```
✅ GET /clans/<tag>
   Clan: Broken Arrow (level 27) — 44 members, 793W
✅ GET /clans/<tag>/members
✅ GET /clans/<tag>/warlog
✅ GET /clans/<tag>/currentwar/leaguegroup
✅ GET /clans/<tag>/currentwar
```

If you get **❌ HTTP 403**, your IP doesn't match the token's whitelisted IP. Regenerate the token with your current IP (`curl ifconfig.me`).

If the CWL leaguegroup returns 404, that's fine — CWL only runs the first ~10 days of each month.

### 6. Run the unit tests

```bash
pip install pytest
pytest tests/ -v
```

Eight tests should pass. They run against fixture JSON, no API calls.

### 7. Try the war report CLI

```bash
python run_war_report.py
```

This dumps the full markdown report for your most recent war (whatever state it's in).

---

## Wire it into Claude Desktop

### Option A — Use the included installer (recommended)

```bash
python install_to_claude_desktop.py
```

This:
- Backs up your existing `claude_desktop_config.json` (timestamped)
- Appends a `coc` entry without touching any other MCPs
- Pulls the token from your `.env`
- Idempotent — re-running just updates the entry

Then **restart Claude Desktop**.

### Option B — Manual edit

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "coc": {
      "command": "python3",
      "args": ["/absolute/path/to/coc-mcp/coc_mcp_server.py"],
      "env": {
        "COC_API_TOKEN": "eyJ0eXAi...",
        "COC_DEFAULT_CLAN_TAG": "#YOURTAG"
      }
    }
  }
}
```

Restart Claude Desktop. The `coc_*` tools will appear.

### Cowork or other MCP clients

The MCP runs over stdio. Anything that supports stdio MCP servers will work — point it at `coc_mcp_server.py` with the env vars set.

---

## Customizing the rubric

The default rules live in `config/rubric.default.json`:

```json
{
  "war_attack_rubric": {
    "first_attack": {
      "acceptable_offsets": [0, 1],   // mirror or one down
      "min_stars_for_pass": 2
    },
    "second_attack": {
      "preferred_offsets_min": 1,     // must attack lower than your rank
      "must_3_star": true             // smart 3-star expected
    },
    "missed_attack_penalty": -10
  },
  "elder_promotion_rubric": {
    "min_donation_ratio": 0.4,
    "min_hero_progress_pct": 80,
    "min_war_score_last_n": 50
  },
  "carry_forward_rubric": {
    "min_attacks_used_pct": 100,
    "min_avg_stars": 2.0,
    "min_avg_destruction": 75
  }
}
```

**Two ways to customize:**

1. **Permanently** — copy to `config/rubric.local.json` (gitignored), edit, then point to it:
   ```bash
   COC_RUBRIC_PATH=./config/rubric.local.json
   ```

2. **Per call** — when invoking `clash_grade_war` or `clash_war_report`, pass `rubric_overrides` with just the fields you want to change. They're deep-merged onto the loaded rubric.

   Example: relax the smart-3⭐ rule for one analysis:
   ```json
   { "war_attack_rubric": { "second_attack": { "must_3_star": false } } }
   ```

---

## Limitations (be aware before you start)

- **Per-attack data is only available for the *current* war** (state `inWar` or `warEnded`). The `/warlog` endpoint returns summary only (result, totals — no per-attack data). To analyze past wars over time, snapshot `/currentwar` to disk while state is `warEnded`. (Future enhancement: a `clash_snapshot_war` tool.)
- **The API is read-only.** You cannot promote, kick, accept join requests, edit clan settings, send messages, or change war state. Supercell intentionally doesn't expose write endpoints. This is a diagnostic / advisory tool — it tells you who deserves the action; you take it in-game.
- **No clan chat access.** Supercell doesn't expose chat. Use a Discord bridge bot if you need chat-based workflows.
- **API token is IP-whitelisted.** Token breaks if your IP changes. Regenerate or use a fixed-IP host.
- **Tenure / "days in clan" is not in the official API.** That data lives on third-party sites like clashofstats.com. Future enhancement could scrape it; for now, the elder rubric uses war contribution and donation ratio as proxies.

---

## Project layout

```
coc-mcp/
├── coc_mcp_server.py             # FastMCP entry — tool registrations
├── coc_mcp/
│   ├── client.py                 # Async COC API client
│   ├── grading.py                # Rubric engine
│   ├── reporting.py              # Markdown generators
│   └── config.py                 # Token + rubric loading
├── config/
│   └── rubric.default.json       # Default rubric (edit freely)
├── tests/
│   ├── fixtures/                 # Sample war JSON for tests
│   └── test_grading.py           # Unit tests
├── coc_test.py                   # Live smoke-test script
├── run_war_report.py             # CLI: dump war report for current war
├── install_to_claude_desktop.py  # Safe installer (backs up first)
└── mcp_config_example.json       # Drop-in for claude_desktop_config.json
```

---

## Contributing

PRs welcome — especially:

- **More workflow tools** for common leadership tasks (war post-mortem trends, donation watch, hero progression alerts)
- **Tenure scraping** from clashofstats.com or similar (worth its own MCP)
- **Snapshot store** for historical war analysis (sqlite or jsonl)
- **War prediction** (given roster + opponent, what's the expected outcome?)
- **More language SDKs** (this is Python; a TypeScript port would help)

If you build something off this, drop a link — would love to see what other clans are doing.

---

## License

MIT — see [LICENSE](./LICENSE).

---

## Acknowledgements

This product uses data from the [Clash of Clans API](https://developer.clashofclans.com) but is not affiliated with, endorsed, sponsored, or specifically approved by Supercell. Supercell is not responsible for it. For more information see Supercell's Fan Content Policy: [supercell.com/fan-content-policy](https://www.supercell.com/fan-content-policy).

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and the [MCP spec](https://modelcontextprotocol.io).
