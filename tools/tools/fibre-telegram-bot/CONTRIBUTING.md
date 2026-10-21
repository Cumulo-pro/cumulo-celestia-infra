# Contributing

1. Fork and create a branch.
2. `pip install -e ".[dev]"`
3. Keep `ruff check`, `ruff format --check`, `mypy` and `pytest` green.
4. Renderers in `src/fibre_bot/reports/` are pure functions: add a fixture-based test for any new field.
5. Never render a missing value as 0 (use `n/a`), and do not use em or en dashes in code, docs or messages.
6. Open a pull request with a short description and a sample of the bot output if it changed.
