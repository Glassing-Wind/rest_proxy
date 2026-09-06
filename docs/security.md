# Dependency Security

The scheduled security workflow runs secret scanning and a blocking `pip-audit`
check. The dependency audit fails CI when it discovers an advisory that is not
in `security/pip-audit-baseline.txt`.

## Existing baseline

The baseline records security debt present when the audit became blocking on
2026-09-05. It is not a declaration that the findings are harmless. The current
flat dependency set includes browser automation, document crawling, ML, and MCP
runtime packages, so resolving the baseline requires coordinated upgrades and
regression testing rather than blind major-version bumps.

When upgrading a vulnerable package:

1. Update both `requirements.txt` and `requirements-ci.txt` where applicable.
2. Remove the resolved advisory IDs from `security/pip-audit-baseline.txt`.
3. Run `./scripts/run_dependency_audit.sh`.
4. Run `./scripts/run_ci_checks.sh` and the retrieval quality gate.

New baseline entries require a short rationale in the pull request and should be
time-bounded. VCS dependencies are excluded from registry advisory resolution;
the pinned ts-pack revision is instead covered by its dedicated build and API
contract checks.

## September 6 review

Secret scanning now uses the pinned Gitleaks CLI directly with release checksum
verification and redacted output; it scans all fetched Git history. The wrapper
Action required an organization license before it could scan. See the
[Gitleaks project](https://github.com/gitleaks/gitleaks) and
[Action licensing notice](https://github.com/gitleaks/gitleaks-action).

The local scan found one historical Tavily-shaped credential at
`04667ea6a080c8290926884d5d5ac79d74693fbb:session-ses_2b4d.md:768`.
The user has not confirmed revocation. No exception was added and history was not
rewritten. Rotate/revoke it before deciding how to handle the historical finding.
The candidate dependency batch is documented under `security/batches/` and is not
applied to the running environment.
