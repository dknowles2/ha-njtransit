# Contribution guidelines

Contributing to this project should be as easy and transparent as possible,
whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## GitHub is used for everything

GitHub hosts the code, tracks issues and feature requests, and is where pull
requests are accepted. Pull requests are the best way to propose changes.

1. Fork the repo and create your branch from `main`.
2. Read [SPEC.md](SPEC.md) if your change touches the API layer. It explains
   why several non-obvious decisions were made, and re-deriving them is
   expensive.
3. If you've changed behaviour, update the documentation — including SPEC.md
   when the change contradicts it.
4. Add tests. See "Fixtures" below before touching `tests/fixtures/`.
5. Make sure the checks below pass.
6. Issue that pull request!

## Development setup

```sh
uv venv
uv pip install -r requirements.txt
```

Run the same checks CI runs:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .    # `ruff format .` to fix
uv run mypy .
```

## Fixtures

`tests/fixtures/` contains a coherent capture of every query, recorded within
the same minute during a live Morris & Essex disruption. It encodes a specific
real-world disagreement between the two NJ Transit feeds, which is the reason
this integration exists.

**Do not regenerate fixtures to make a failing test pass.** If a test fails
against them, either the code is wrong or the API changed — and if the API
changed, that deserves its own PR with a fresh coherent capture and a note in
SPEC.md, not a quiet overwrite.

## Reporting bugs

Report a bug by [opening a new issue](../../issues/new/choose).

Great bug reports tend to have:

- A quick summary and background
- Steps to reproduce, being specific
- What you expected to happen
- What actually happened
- Notes — anything you tried that didn't work, or a theory about the cause

Because this integration talks to an undocumented endpoint that can change
without notice, **diagnostics output is especially useful** — download it from
the integration's device page and attach it. It includes the raw payloads and
requires no redaction: the API carries no credentials and no personal data.

## Coding style

Style and import sorting are enforced by `ruff format` and `ruff check`. Don't
hand-format — run the tools.

## License

This project is licensed under the [Apache License 2.0](LICENSE). By
contributing, you agree that your contributions will be licensed under it.
