# Security policy

## Reporting

Report suspected vulnerabilities privately through [GitHub Security Advisories](https://github.com/kas1987/Syn_Studios/security/advisories/new). Do not publish credentials, private submission material, sensitive locators, exploit details, or raw logs in an issue or pull request.

## Supported branch

The `main` branch is the only supported branch.

## Controls

- Pull requests and pushes to `main` run the repository contract on Linux and Windows.
- CodeQL runs on pull requests, pushes to `main`, a weekly schedule, and manual dispatch.
- Dependency review checks pull requests; Dependabot monitors Python validation dependencies and GitHub Actions.
- Actions use immutable commit pins and read-only permissions unless a job requires a narrower write permission.
- `main` requires the stable `contract` status check, linear history, resolved review conversations, administrator enforcement, and protection from force-pushes or deletion.

Security automation reduces risk but does not authorize publishing source artifacts or private facts. The integrity boundaries in `SYNTHETIC_DESIGN.md` remain controlling.
