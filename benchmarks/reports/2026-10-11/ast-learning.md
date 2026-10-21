# Syntax and repository learning — current-source investigation

October 11, 2026. Read-only investigation using the GraphRAG MCP tools, following
the user's clarification that RepoAnalyzer and GithubAnalyzer go beyond semantic
retrieval and encode text into AST structures. This is source evidence, not a new
runtime acceptance or storage benchmark.

## Tool use and freshness

`get_indexing_health`, `get_project_overview` and `get_indexed_projects` established
the available graph context. Rest-proxy's structural/semantic runs were aligned,
but health reported 41 stale and 27 missing files. RepoAnalyzer and GithubAnalyzer
were unindexed. Indexed search, symbol context and graph provenance were used for
orientation; `describe_file(include_source=True)`, `grep_codebase` and
`extract_function_body` supplied current source for the conclusions below.

One native directory discovery located the GithubAnalyzer checkout after project
and index inventories omitted it. No repositories were reindexed, no models were
loaded and no services were restarted. The sibling analyzer checkouts were read
without changes. Git and documentation maintenance used ordinary local tools.

## Verified representations

**Rest-proxy:** native ts-pack parses source into Tree-sitter syntax structure,
then derives declarations, imports, call observations and syntax-aware chunks.
The embedded publisher retains original source/hashes and selected symbol/fact
relationships. Chunk embedding is a separate operation. Its persisted graph is a
source/fact representation, rather than a serialization of every syntax-tree node.
Call observations retain a `syntactic-observation` resolution label; that is weaker
than proof of the runtime callee. Explicit call query coverage includes Python,
JavaScript, TypeScript and TSX. File-role metadata also includes heuristic inference.

**RepoAnalyzer:** `CodePatternArtifact` can retain both an AST and an optional
embedding. The reference extraction path observes repeated structural nodes and
builds a recursive syntax-tree dictionary from a bounded code sample (the first
1,000 characters), separately from embedding that sample. Its reference workflow
extracts and persists code, documentation and architecture patterns, then uses
stored patterns and repository comparisons to generate recommendations.

Its custom plaintext parser creates a `document` root and children from enhanced
pattern matches, retaining content, source positions, metadata and relationships.
Thus AST-like representation includes non-code text. The default local repository
context adapter also uses lightweight regex-based extraction; complete AST coverage
of every ingestion path is not established by these source reads.

**GithubAnalyzer:** `RepoProcessor.process_repo` invokes parsing, AST extraction and
storage. Structural graph construction creates file/function nodes and call links.
The wired GraphCodeBERT embedding service uses pretrained inference (`eval()` and
`no_grad`) over code text; this path does not receive the AST. Pattern registration
includes authored Tree-sitter queries. The bounded audit found no model-training
loop or equivalent RepoAnalyzer reference-learning workflow in this checkout.

## Concrete follow-up

GithubAnalyzer's processor imports `node_to_dict` from
`services/analysis/parsers/utils.py`. That serializer returns type, text, source
points and bytes, but omits `children`. The processor's function extraction traverses
`children`. A different recursive serializer exists in `models/core/ast.py`, but
the inspected processor does not import it. This suggests nested structural evidence
can be lost on the active path. It is a code-informed issue; no reproduction or
repair was performed in this Rest-proxy task.

At the owning analyzer, verify nested functions/classes and call spans through the
actual parse → serialize → persist → query path before changing the serializer.
For integration, retain source hashes/spans, parser/backend identity, syntax kind,
relationship resolution and pattern origin independently of encoder identity.
Distinguish observed source facts from pattern candidates and recommendations.
Include plaintext structure and recommendation evidence in quality evaluation,
alongside lexical/vector recall and controlled coding outcomes.

## Current source references

Paths below are relative to the named local checkout. Analyzer sources are sibling
projects, not new Rest-proxy dependencies. Hashes identify the files returned by
the MCP current-source reader; line numbers refer to those snapshots.

| Checkout | Source | Relevant lines | SHA256 |
| --- | --- | --- | --- |
| RepoAnalyzer | `parsers/custom_parsers/custom_plaintext_parser.py` | 156–234 | `cadb8908aa24b336ea556de263838b112aa0b7d65f54273f693c42422d924592` |
| RepoAnalyzer | `learning_core/artifacts.py` | 32–68 | `9c5e5636b56708e64751211d743dccc4da5f194ddfd7437549ce5df2c5d65131` |
| RepoAnalyzer | `learning_core/extractors.py` | 68–104, 279–306 | `9b76cf015ab3fe139f7b5b189e8a81794a9b8e8d44baa3a31dedb36c659b1cbb` |
| RepoAnalyzer | `learning_core/workflows.py` | 39–110 | `ad0446af23a3b2d4b0af04c7bcb4cadd7fdebabd1ad40ecc41e1e3a1812038cd` |
| RepoAnalyzer | `ingestion/local_repository.py` | 100–183 | `c7edce6cc848e35e5700603b000dc6f00ac26ddaf315759ecd2c252700dd9dd1` |
| GithubAnalyzer | `src/GithubAnalyzer/services/core/repo_processor.py` | 27–29, 170–176, 367–428 | `90683ff7c38433e38e48b5f0e0344120b347ae7cf99e7d81ee1de59625301b9d` |
| GithubAnalyzer | `src/GithubAnalyzer/services/analysis/parsers/utils.py` | 84–93 | `02ed06bb7e96714edcc74095c8996d7dbbe95f480559832272ed30666f313ba3` |
| GithubAnalyzer | `src/GithubAnalyzer/services/core/database/embedding_service.py` | 42–44, 101, 136 | `d6e687f9975aa530225f35152d2251a5f67bcd9802ceb1535c7b7c9bf6982d1a` |

Rest-proxy implementation references:

- [Native source outlines](../../../graphrag_core/indexing/embedded_outlines.py): parsing, symbol spans and graph publication.
- [Parser facts](../../../graphrag_core/indexing/embedded_facts.py): import/call observations and resolution labels.
- [Embedded repository](../../../graphrag_core/indexing/embedded_repository.py): separate structural capture and chunk embedding.
- [Server retrieval indexing](../../../scripts/index_workspace.py): syntax-aware chunking and diagnostic/metadata gates.
- [Structural indexing wrapper](../../../scripts/run_struct_index.py): native indexing, finalization and file-level call links.

These findings refine the [canonical architecture](../../../docs/current-architecture.md).
They do not establish automatic analyzer integration, model adaptation, complete
language coverage or improved end-to-end coding outcomes.
