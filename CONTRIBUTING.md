# Contributing

Thanks for considering a contribution! This project is a small Python MCP server, so it's easy to hack on.

## Development setup

```bash
git clone https://github.com/justinritchie/clash-of-clans-mcp.git
cd clash-of-clans-mcp
pip install -r requirements.txt pytest
cp .env.example .env  # add your token
```

## Running tests

```bash
pytest tests/ -v
```

The tests use fixture JSON in `tests/fixtures/` so they don't hit the live API. Add a fixture if you need to test a new scenario.

## Live smoke test

```bash
python coc_test.py
```

## Code style

- Type hints throughout
- Pydantic v2 for input validation on MCP tools
- Async/await for all network I/O
- Docstrings on every tool — first paragraph becomes the MCP tool description

## Areas where contributions are most welcome

- **More workflow tools** — common leadership tasks (donation watchlists, hero progression alerts, war prediction)
- **Snapshot store** — sqlite or jsonl logging of `warEnded` snapshots so we can analyze beyond the current war
- **Tenure scraping** — read clan history from clashofstats.com or similar
- **TypeScript port** — for clans that prefer Node
- **Additional rubric examples** — `config/` PRs welcome with named rubrics for different clan styles
- **Bug fixes** — file an issue first, then PR

## Pull requests

- Branch from `main`, name it `feature/...` or `fix/...`
- Include or update tests
- Update README if you add a new tool or change behavior
- One concern per PR — easier to review

## Reporting issues

Include:
- Output of `python coc_test.py` (with token redacted)
- The MCP tool you called and the params
- Expected vs actual behavior
- Python version (`python --version`)
