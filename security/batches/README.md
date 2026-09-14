# Prepared HTTP dependency batch — 2026-09-06

Status: prepared and resolver/audit checked; **not applied or runtime validated**.
Apply `2026-09-06-http-clients.patch` on a separate branch after the evaluation pilot.
The patch updates both requirement files and removes only 20 resolved advisory IDs
from the existing baseline. It does not change the running environment.

| Package | Existing | Candidate | Candidate advisories |
|---|---|---|---:|
| aiohttp | 3.13.4 | 3.14.3 | 0 |
| python-multipart | 0.0.26 | 0.0.31 | 0 |
| urllib3 | 2.6.3 | 2.7.0 | 0 |

The full registry dependency resolver dry run succeeded on local Python 3.11/macOS
with `--ignore-installed`; VCS ts-pack was excluded as in the current audit script.
The audit changed from 116 advisory rows across 19 packages to 95 across 16 packages.
Twenty unique advisory IDs account for 21 removed rows because the audit includes
duplicate advisory entries. This is preparation evidence, not proof of compatibility
on Linux or proof that the remaining baseline is acceptable.

Upstream package metadata checked:
[aiohttp](https://pypi.org/pypi/aiohttp/3.14.3/json),
[python-multipart](https://pypi.org/pypi/python-multipart/0.0.31/json),
[urllib3](https://pypi.org/pypi/urllib3/2.7.0/json).
Audit summaries are in `2026-09-06-http-clients-audit.json`.

Before adoption:

1. Create an isolated candidate environment; apply the patch and install the candidate
   requirements plus the pinned ts-pack wheel. Do not upgrade the shared brain runtime.
2. Run dependency audit using the reduced baseline and both requirement surfaces.
3. Run full `scripts/run_ci_checks.sh` and `scripts/run_retrieval_quality_gate.sh`,
   then hosted CI on Python 3.11/Linux. The MCP protocol suite restarts its server,
   so use an isolated server/ports or a quiet window.
4. Exercise HTTP multipart parsing and client connections/redirects at integration
   boundaries, and review new limits for compatibility with legitimate requests.
5. Only then adopt the patch. Keep MCP (candidate >=1.28.1) and Starlette
   (candidate >=1.3.1) in a separate FastAPI/MCP compatibility batch; a Starlette
   major-version move must not be folded into this small preparation patch.
