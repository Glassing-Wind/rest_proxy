# MCP benchmark measurement notes

Revision: b05363c4b43df110f749a0a238fc0589bd76a49f. Condition: MCP with explicitly counted native fallbacks. One fresh subagent condition context; no other answers/results consulted. Fifteen cases were investigated in interleaved batches, not fifteen isolated agent sessions. expected_evidence path hints from the case file guided retrieval.

## Measurements and limitations

- Actual model token telemetry was unavailable. Integer token fields are estimates: input = ceil((case prompt characters + each tool argument JSON characters + canonical tool response text characters)/4); output = ceil(answer characters/4). These measure payload volume only, excluding reasoning, history replay, system context, caching and duplicate response envelopes. No claim of billing savings is justified.
- elapsed_seconds sums Date.now-measured durations around each case's tools. It excludes reasoning, orchestration and answer writing, and is NOT an end-to-end time-to-answer measure. Calls across cases ran concurrently. Overall elapsed since setup marker at report construction: 258.013 seconds; initial discovery/setup preceded that marker.
- setup_seconds 7.233 measures time from the marker after schema reading through the health check. Initial native setup used 3 exec_command calls (AGENTS+cases+file inventory; schema+revision; repeated cases parsed to orchestration state) and tool discovery, not included per-case. No indexing was run; indexing_seconds = 0, no measured cold-index cost.
- tool_calls counts each substantive MCP/native invocation once, including failures. It excludes functions orchestration, rereading stored outputs, report writing and validation. Native fallback calls have a separate per-run count. Tool-internal disk/grep access is MCP activity, not an agent-native fallback.
- files_opened counts unique files whose source content or search snippets were returned per case, including line-oriented searches; excludes path-only definition results and failed nonexistent file opens. Backend physical reads are not observable.
- Full tool responses are recorded, though some combined model displays were truncated; stored relevant responses were redisplayed to inspect answers. Token estimates use canonical full returned text and may overstate text actually consumed.
- Correctness is self-assessed with source evidence pending independent parent grading. No unsupported assertions intentionally included; statement completeness should be reviewed separately.

## Index state

Existing warm index: 283 disk, structural and semantic files, aligned done runs; 200/200 source parse success and 283/283 support coverage reported. Health is a level-1 check, not deep audit. Global shadow residue: one project namespace with 4478 nodes and 1031 relationships. Exact-name find_definitions surfaced that shadow namespace and an overlapping nested proxy project, so warm/healthy does not mean globally clean or perfectly disambiguated. No reindex or cleanup performed.

## Observations

Semantic search sometimes returned only one chunk despite k=3/4; code-ranking query returned no match under its path filter. MCP source extraction recovered exact bodies efficiently. Native reads filled module-level/line-evidence gaps. Failures are included in metrics. Fingerprint answer follows actual .py collector behavior rather than assuming listed requirements manifests are hashed.

