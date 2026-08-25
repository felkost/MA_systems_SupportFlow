# Contributing

## Gate chain

```bash
black --check . tests/*.py && flake8 && mypy src && pytest --cov=src
```

All four must pass before a PR is opened. `deepeval test run tests/` is a
separate, slower gate — required before a stage closes, not on every commit.

## Language policy

- Chat/session language: Ukrainian.
- Everything tracked (code, comments, docstrings, commit messages, PR
  titles/bodies, README, `docs/`): English.

## Branches and commits

One change — one branch — one PR. Branch names describe the change
(`feat/router-agent`), never a stage number. Commit messages are
imperative and describe the change by substance
(`Add router agent and message schemas`), never by stage
(`~~Stage 1: core~~`).

`main` accepts no direct pushes — branch protection requires a PR.
The assistant never runs `git commit` / `git push` / `gh pr create`;
it prints the exact commands and the author runs them.

## File size

Files ≤250 lines are preferred; 250–320 lines are acceptable for a single,
well-scoped responsibility; anything larger is split (task §8).

## AI disclosure

Recorded in the stage report, not in a commit trailer — a `Co-Authored-By`
trailer becomes a permanent, hard-to-remove contributor-graph entry.

## Dependencies

`requirements.txt` is runtime; `requirements-dev.txt` is gate tooling only.
Every pin is a version this project has actually measured working, not a
floor. Before changing a pin: `pip freeze` before and after, diff it —
installing one package can silently downgrade another.
