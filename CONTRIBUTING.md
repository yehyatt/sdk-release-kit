# Contributing

Thanks for helping. Issues and pull requests are welcome.

1. Read [AGENTS.md](AGENTS.md); its hard rules apply to everyone, human or AI.
2. Set up: `python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'`.
3. Use a scratch settings directory while developing:
   `export SDK_RELEASE_KIT_HOME=/tmp/srk && sdk-release-kit init`.
4. Before opening a pull request: `ruff check src` and `sdk-release-kit build --dry-run`.
5. Never include real credentials, client names, real bundle ids or card numbers in code,
   examples, screenshots or issues. Use `com.example.*` identifiers.

Keep pull requests small and describe how you verified the change.
