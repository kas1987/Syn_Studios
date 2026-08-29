# Contributing to Syn Studios

Syn Studios is a public, source-first design system. Contributions must remain reusable, reviewable, and free of private submission content.

## Flow

1. Open or select one focused issue.
2. Create a branch named `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `test/<topic>`, or `chore/<topic>`.
3. Make one narrow change and use a conventional commit.
4. Run the local proof below.
5. Open a pull request and complete the integrity checklist.
6. Merge only after the protected `contract` check passes and review conversations are resolved.

Use `<type>(optional-scope): <imperative description>` for commits. Supported types include `feat`, `fix`, `docs`, `test`, `refactor`, `ci`, `build`, and `chore`.

## Local proof

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall -q scripts tests
python -m unittest discover -s tests -v
python scripts/validate_library.py
```

These are the same substantive checks run on Linux and Windows in CI.

## Integrity boundary

- Work only with authorized synthetic or fictional materials.
- Do not commit real records, raw submission artifacts, private answers, world facts, credentials, local dumps, or generator residue.
- Record locators, hashes, structural observations, and reusable patterns instead of copying prior artifact bytes.
- Keep each rule in its owning source; link rather than duplicate it.
- Do not modify an active submission while studying it.
- Template creation requires a separately reviewed change and the release evidence required by `SYNTHETIC_DESIGN.md`.
