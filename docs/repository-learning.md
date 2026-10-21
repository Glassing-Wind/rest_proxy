# Repository learning through Rest-proxy

Rest-proxy exposes optional RepoAnalyzer profiles/comparisons, durable pattern
artifacts, custom plaintext AST observations and GithubAnalyzer configuration
observations through shared MCP and REST services. Learning writes go to separate
private SQLite memory. Repository evidence remains under the existing embedded
owner; these operations do not edit source, reindex, load models or apply suggestions.

## Optional setup

Keep the existing [single embedded owner](embedded-mcp-owner.md). Configure its
process with trusted local analyzer checkouts and, for persistent learning, an
explicit private directory:

```sh
export LM_PROXY_REPOANALYZER_ROOT=/path/to/RepoAnalyzer
# Optional compatible interpreter; also used by the GithubAnalyzer component worker:
export LM_PROXY_REPOANALYZER_PYTHON=/path/to/compatible/python
export LM_PROXY_GITHUB_ANALYZER_ROOT=/path/to/GithubAnalyzer
export LM_PROXY_REPOSITORY_LEARNING_STATE=/private/path/to/repository-learning
```

Leave unused options unset. There is no automatic install or dependency download.
Without the corresponding analyzer root, its operation returns a disabled or
unavailable status. Without learning state, persistent operations are disabled and
mutation tools are not registered. Profile/comparison reads remain independent of
learning memory. Interpreter selection falls back to the selected checkout's
`.venv/bin/python`, then the application's current Python.

The learning directory must have mode **700**, contain no symlink path components,
and remain outside tracked/shared source. An explicit mutation can create it and
`learning.sqlite3` with mode **600**; inspection does not create state. Provided
plaintext/configuration originals are retained there privately. Source IDs are
labels, never filesystem paths to open.

Tools register with embedded storage and an explicit `LM_PROXY_EMBEDDED_STATE`.
`LM_PROXY_EMBEDDED_REST_ENABLED=1` exposes `/evidence/read` on the HTTP MCP brain;
an explicit learning-state directory additionally exposes `/learning/write`.
The latter has a fixed four-operation allowlist, rather than general tool/admin
dispatch. The inference proxy is a separate application. Apply configuration to
the intended owner process; do not launch another writable embedded owner against
its directory. These loopback routes do not establish multi-user authentication.

## Operations

REST sends `{ "tool": "name", "arguments": { ... } }`. Its JSON matches MCP structured
content, including an explicit operation status; inspect that status even when
HTTP is 200. Schema/transport errors use HTTP 4xx; unavailable tools or transport
execution failures use 503. Backend/storage failures also have explicit JSON statuses.

| MCP tool / dispatcher name | REST route | Arguments |
| --- | --- | --- |
| `get_repository_learning_profile` | `/evidence/read` | `project_id`, optional `max_files` |
| `compare_repository_learning_profiles` | `/evidence/read` | `reference_project_id`, `target_project_id`, optional `max_files` |
| `get_learned_repository_patterns` | `/evidence/read` | `project_id`, optional positive `revision`, `limit`, `offset` |
| `suggest_learned_repository_patterns` | `/evidence/read` | `reference_project_id`, `target_project_id`, positive `reference_revision`, optional `max_files` |
| `learn_repository_patterns` | `/learning/write` | `project_id`, `expected_run_id`, `expected_revision`, optional `max_files` |
| `learn_text_patterns` | `/learning/write` | `project_id`, `source_id`, `text`, `expected_revision` |
| `learn_configuration_patterns` | `/learning/write` | `project_id`, `source_id`, `text`, `parser` (`requirements` or `gitignore`), `expected_revision` |
| `delete_repository_learning` | `/learning/write` | `project_id`, positive `expected_revision` |

Read tools remain available in compact discovery. Configuring learning state also
registers explicit mutation tools with MCP write/destructive annotations and admin
catalog entries. They are excluded from the deep-read dispatcher and `/evidence/read`.

For repository learning, inspect `get_embedded_overview` for its publication run and
`get_learned_repository_patterns` for the memory revision. New memory reports zero.
Supply both exact values:

```sh
curl http://127.0.0.1:8001/learning/write \
  -H 'Content-Type: application/json' \
  -d '{"tool":"learn_repository_patterns","arguments":{"project_id":"reference","expected_run_id":"RUN_FROM_OVERVIEW","expected_revision":0,"max_files":20}}'
```

Use the returned revision for subsequent writes or historical reads. Stale memory
returns `conflict`; a changed/deleted repository publication returns
`publication-conflict`. Inspect the changed state before retrying. Text/configuration
learning requires a memory revision and explicit `provided-text` provenance; it
never claims to read a published/live file. Repository learning requires at least
one selected published file; generic profile reads retain their empty-publication status.

## Durable artifacts and history

Repository learning retains normalized file-language, declaration-kind and naming
observations with per-source occurrences, source hashes, captured declarations,
publication/context identity, analyzer fingerprints and profile inference. This is
more than saving a raw profile. Counts and `distinct_sources` describe that artifact's
evidence, not cumulative request counts or quality scores.

Changed artifacts append immutable revisions and become the project's head. An
identical artifact with the correct expected revision returns `unchanged` without
recounting. Repository, provided-text and configuration records share the project's
revision sequence; another kind changes the head while older revisions remain
selectable. Stored repository reads include the historical profile, coverage and
publication identity alongside patterns. All reads identify the findings' status.

Embedded reindex/deletion does not delete learning history. Retained repository
context/outlines/citations can support reference comparison against a new target
publication. Original repository file bodies are **not** copied into learning memory;
after graph deletion they may be unavailable. Supplied text/configuration originals
and their artifacts are retained privately. Reads disclose historical freshness.
Text/configuration transfer compares retained artifacts of the same kind. Candidates
mean “not observed in selected target evidence,” not repository-wide absence.

`delete_repository_learning` removes all retained payload revisions for that project
and advances a tombstone revision. The fence remains, so an old writer cannot
recreate deleted history using zero. A later explicit write must use the current
tombstone revision. Learning deletion leaves source/graph/retrieval publications intact.

## Parser and evidence scope

Profile/comparison capture runs in one read transaction under the existing runtime/
owner locks, then releases them before analysis. Source, parser-fact and declaration-
outline hashes are verified. Results retain the captured run even when reindexing
finishes during analysis. Repository learning commits recheck run/manifest under the
owner lock before the SQLite revision transaction; changes refuse the commit.
Older publications without `outline_sha256` return
`reindex-required-for-outline-provenance` here; ordinary source/search reads remain
available. No automatic reindex occurs.

Files are selected in path order, with included/omitted counts and explicit partial
coverage. Selected files retain all their declarations within the limit. The graph
stores selected syntax facts, not every native AST node; historical parser binary
identity was not recorded in those publications. Analyzer conclusions remain
inference, separate from verified captured facts.

Plaintext learning invokes RepoAnalyzer's **actual authored-pattern regex extractor
and node factory**. It retains a custom document AST, matched children, source hashes,
character/UTF-8 byte spans, Unicode point units and pattern counts. This slice excludes
full parser initialization, adaptive matching and AI initialization. Authored
`MULTILINE|DOTALL` behavior remains: matches can overlap and list/metadata matches may
span later lines. Relationship templates name pattern kinds, not resolved graph/
runtime edges. Unmatched text remains in the document; paragraph coverage is not implied.

GithubAnalyzer learning invokes its **actual `RequirementsParser` or `GitignoreParser`**.
Five fixed unmodified component/dependency files are source-hashed and loaded from
disposable scratch; broad package startup is skipped and logger writes stay there.
No database API is mocked or opened. Requirements support simple `>=`, `==`, `<=` and
editable declarations. `latest` is its unpinned default label, not a queried release;
constraint names retain the raw `-c ...` form. This is not complete PEP 508 parsing or
dependency solving. Ignore flags preserve component behavior, including stripping
repeated leading exclamation marks; they do not evaluate effective Git ignore rules.
No clone/acquisition or full GithubAnalyzer runtime is included.

## Bounds and acceptance

| Bound | Current value |
| --- | --- |
| Repository files | Default 20 per scope; strict integer `max_files` 1..64 |
| Declarations | At most 128 per selected file |
| Supplied plaintext/configuration | Nonempty MCP/REST text, at most 16 KiB UTF-8; source ID at most 256 characters |
| Plaintext AST | Document root plus at most 128 matched child nodes |
| Configuration observations | At most 256 requirement/constraint or ignore declarations |
| Pattern inspection | Default `limit=20`, integer 1..50; `offset` 0..1024 |
| Worker | 15 seconds; 256 KiB request; 32 KiB result |
| REST | Read body 16,000 bytes; write body 20 KiB; response 48,000 bytes |
| Private memory | 512 KiB record; 128 projects; 128 retained revisions per project; 1,024 source/pattern observations per record |

Limits fail explicitly without automatic eviction. Reduce `max_files` for excessive
normalized observations and inspection `limit` for large cited/profile responses.
A maximum-sized input may exceed its result budget, and escaped text/JSON framing
may exceed the REST body limit. Revisions/bounds reject boolean/string/float coercions.

POSIX timeout/cancellation/protocol cleanup terminates and reaps the owned worker
process group. Provider/storage credentials are excluded from worker environments.
Network attempts and learning-runtime initialization are refused; backend writes
stay in disposable scratch. Administrator-selected code remains trusted: these
guards are **not an OS sandbox**. Artifact persistence/extraction does not establish
adaptive training, model-weight updates, correctness, performance gains or better
coding outcomes.

See [current architecture](current-architecture.md),
[initial profile acceptance](../benchmarks/reports/2026-10-12/repository-learning/README.md)
and [persistent-learning acceptance](../benchmarks/reports/2026-10-12/persistent-learning/README.md).
