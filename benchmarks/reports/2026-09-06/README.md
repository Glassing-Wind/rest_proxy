# Native vs MCP: 15-case pilot, 2026-09-06

The pilot does **not demonstrate that MCP is faster or uses fewer model tokens**.
Both agents recorded correct, bounded answers for all 15 cases. These are self-grades,
not blinded independent accuracy scores. MCP used 57 investigation calls versus 40
native calls, including 18 native fallbacks in 11 of 15 cases. It exposed fewer files
per case (28 summed distinct-per-case files versus 38), but estimated payload tokens
were slightly higher (60,784 versus 58,577). Those estimates are not model usage and
use slightly different input accounting, so they cannot establish token-cost savings.

The conditions ran in fresh, non-inheriting agent contexts at source revision
`b05363c4b43df110f749a0a238fc0589bd76a49f`, with expected-evidence hints visible to both.
Native cases were sequential; MCP cases were interleaved. Both shared machine/cache
resources and retained within-condition context. Native recorded end-to-end case time;
MCP recorded tool-call latency. The comparator preserves those original observations
and suppresses timing deltas instead of presenting a false speed comparison. Neither
condition captures complete setup overhead or a cold indexing cost. The native agent
hit its usage limit after saving all 15 records and its transcript; these saved records
were recovered and schema-validated without substituting new answers.

## Artifacts and review

- `native-results.json`, `mcp-results.json`: complete schema-valid original answers.
- `native-transcript.json`, `mcp-evidence-log.json`: recorded inspection calls/results.
- `native-notes.md`, `mcp-measurement-notes.md`: methods and limitations.
- `comparison.json`, `scorecard.md`: comparison covering every paired case.

Parent review checked every cited file/line exists and reviewed answers and recorded
inspection evidence. Targeted direct source checks confirmed the subtle fingerprint,
cross-project name matching, and graph-required symbol lookup limitations. This is
not a blinded full claim-by-claim correctness audit or an outage/integration test.
The recorded true/zero-unsupported fields remain the investigators' self-assessments.
Keyword evidence coverage (90% native, 93.3% MCP) penalizes synonymous wording such
as “best effort” versus “best-effort”; it is not a semantic accuracy measure.

## Every-case classification

These classifications use visible call counts, file exposure and payload proxies;
“win” is local to those metrics, not a measured latency or model-usage claim.

| Case | Classification | Evidence/reason |
|---|---|---|
| http_mcp_startup | Native tooling win | 3 vs 5 calls; MCP needed 2 native reads to complete startup/module evidence. |
| proxy_chat_flow | Index-quality failure; native tooling win on calls | First path-filtered search returned the health snippet; exact lookup returned active, nested and shadow definitions. 4 vs 5 calls. |
| memory_mode_gating | Roughly equivalent | 2 calls and 1 file each; MCP still needed native config lines. |
| index_job_recovery | Native tooling win | 2 vs 4 calls; first semantic result foregrounded maintenance rather than state recovery. |
| semantic_search_ranking | Tool-selection/retrieval failure | Path-filtered search returned no match in a warm indexed file; outline/body extraction recovered it, then native follow-up. 3 vs 5 calls. Root cause not yet isolated. |
| duplicate_suppression | Native tooling win | 2 vs 5 calls, 2 files each; exact source required after helper-focused semantic hit. |
| documentation_pipeline | Tool-selection failure; native tooling win | Search restricted to indexer facade could not cover multi-file pipeline; 3 vs 5 calls, 5 files each. |
| symbol_context | Index-quality failure; mixed efficiency | Exact lookup exposed shadow duplicate; MCP showed 2 vs 4 files but took 4 vs 3 calls. |
| test_coverage_lookup | Clear MCP win on observed calls | 2 vs 3 calls, 1 file each; signature hit plus exact body yielded fuller bounded answer. |
| watcher_lifecycle | Index-quality failure; native tooling win | Shadow duplicate and native line reads; 3 vs 5 calls. |
| stale_mcp_session | Native tooling win | 2 vs 3 calls; MCP used two native reads. Both correctly distinguish SDK rejection from diagnostic middleware. |
| tool_fingerprint | Native tooling win; excessive output cost | 1 vs 2 calls; MCP proxy tokens 2,089 vs 1,232. Both catch the requirements-file omission. |
| cross_project_trace | Roughly equivalent / tradeoff | MCP 3 vs 2 calls with 148 fewer proxy tokens; same one file; both identify target-name ambiguity. |
| optional_dependency_failure | Tool-selection failure; excessive output cost | Guessed nonexistent symbol and failed native path; 5 vs 4 calls, 787 more MCP proxy tokens, 3 native fallbacks. |
| tool_choice_catalog | Clear MCP win on observed calls/files | 2 vs 3 calls, 1 vs 2 files, 1,267 fewer proxy tokens; both explain guidance rather than enforced routing. |

No incorrect answer was established in this limited review. That is weaker than
proving zero unsupported claims. Do not turn these single observations into thresholds.

## Three highest-value improvements

1. **Return bounded, citation-ready source including module configuration.**
   Eleven cases required 18 native fallback calls. Config/memory gating, startup,
   stale-session handling, watcher lifecycle and fingerprint inspection repeatedly
   needed line-numbered native reads after MCP retrieval. Add or extend a bounded
   file-region/module-level read that returns path, exact span and source revision;
   retain output caps. Validate that these specific cases can obtain complete
   evidence without native reads and without returning whole unrelated modules.

2. **Route known-file and multi-file questions to the right evidence operation.**
   Search was used first in all 15 MCP cases even where expected file/symbol hints
   were available. The ranking query returned empty; chat-flow search exposed health;
   job-recovery search foregrounded maintenance; docs search was restricted to a facade.
   These are a mix of agent selection and retrieval failures, not proof that semantic
   ranking alone is wrong. Replay saved queries with scoped symbol/outline/body tools,
   examine index candidates before tuning ranking, and compare complete task costs.
   Add those replays to the tool-choice evaluation rather than broadly changing scores.

3. **Exclude staging namespaces from user-facing definition lookup and expose scope.**
   Three exact-name calls (chat flow, watcher, symbol context) returned the same shadow
   namespace alongside active definitions despite a warm aligned health report. The
   chat-flow lookup also showed a nested proxy project. The log identifies shadow ID
   `6f8dead37cb2::shadow::6f8dead37cb2:41619:1788546631249999872`.
   Keep staging data out of ordinary discovery, show requested/resolved project identity,
   and test active+shadow+nested fixtures. Quiet-window cleanup addresses this residue;
   query isolation prevents interrupted indexing from contaminating future results.

Two additional source findings are worth separate regression cases: default fingerprint
collection drops the listed requirements manifests because it accepts only `.py` files
(`_tool_fingerprint.py:56`); cross-project graph consumers constrain caller project but
match target symbols only by name (`memory/cross_project_trace.py:242`). Neither proves
an MCP advantage: both conditions identified them. Retrieval behavior remains unchanged.

## Next experiment

Use a recorder around every invocation, capture actual runner input/output token usage,
separate setup/indexing, use the same timing boundary, hide expected evidence, and grade
answers independently. Run sequential counterbalanced conditions at a fixed model/tool
configuration, repeat across index states and an unfamiliar repo. Record failures and
abstentions; do not force unknown metrics to zero. See `docs/next-session-roadmap.md`.
