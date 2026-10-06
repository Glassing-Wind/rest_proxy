# Active-branch dependency audit repair — October 5, 2026

The [Security run for workspace-activity commit `a974000`](https://github.com/Glassing-Wind/rest_proxy/actions/runs/37403569682)
failed dependency audit with two new findings:

| Package | Previous pin | Reported advisory | Updated pin |
| --- | --- | --- | --- |
| multidict | 6.7.1 | CVE-2026-104874 | 6.9.1 |
| fsspec | 2026.2.0 | CVE-2026-104851 | 2026.6.0 |

Both production and CI manifests now use the reported fixed versions. The ts-pack
fork pin and security exclusion baseline are unchanged. This failure is on the
active PR #3 branch, distinct from the previously reviewed older PR #1 failures.

Local Python 3.14.4 installation succeeded. `pip check` reports no broken
requirements; fourteen native repository tests and full local CI pass. The same
dependency-audit script passes using an isolated audit-tool environment:
`No known vulnerabilities found, 99 ignored`. This means no unexcluded advisory was
reported at evaluation time, not that every dependency is vulnerability-free.

This turn prioritizes the new security regression. IDE registrar/discovery and
embedded watcher dispatch remain the next integration work; no session registrar
or watcher dispatch behavior is changed by these dependency pins.

[Machine-readable receipt](dependency-refresh.json). Private logs:
`.runtime/dependency-refresh-acceptance/` (mode 700).
