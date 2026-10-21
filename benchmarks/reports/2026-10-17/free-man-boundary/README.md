# Free Man research boundary acceptance

October 17, 2026. The user endorsed separating Rest-proxy's operational role from
Free Man's research on learning, judgment and the longer-term ambition of
superintelligence. This slice establishes a dedicated evidence interface and
controlled lesson artifacts. It does not establish superintelligence, an autonomous
research worker, adaptive learning or an operating-system sandbox.

## Implementation

- A separate low-level MCP server and explicit `scripts.free_man` STDIO launcher
  expose exactly five corpus reads: status, source catalog, source excerpt, lexical
  search and reference occurrences. No operational registry, FIRE, ledger,
  account/provider client or application configuration is imported by this surface.
- The standalone corpus REST reader and research MCP share bounded projections,
  exact citations, source integrity checks and candidate-recovery qualifications.
  HTTP authentication, loopback/Host/Origin checks and response contracts remain.
- A separate lesson reader opens only existing private ledgers with SQLite
  `mode=ro`, `query_only=ON` and read transactions. It shares existing history and
  citation validators without creating storage or schema. WAL-format databases
  and journal/WAL/SHM sidecars are refused; the ledger must be quiescent.
- Review packets bind up to ten current lesson revisions to the exact source
  manifest. Release requires explicit human acceptance of each current revision,
  plus a separate human release of the exact packet, local destination, use and
  rights basis. Bundles have fixed data-only constraints and a 1 MiB budget.
- Bundle inspection checks an explicitly supplied fingerprint, versioned syntax,
  internal hashes and review/release structure. It installs nothing, grants no
  tools, and does not check currentness against the source ledger. Reprepare
  current evidence before downstream use.

Source bodies and authored lesson records stay outside Git. A new owner-only
Free Man review directory holds the exact selection, review packet and the user's
decision to **keep both proposals pending**. No acceptance was inferred from the
architectural agreement. The actual ledger was read without mutation, and no
real lesson release was produced. The two proposals remain at revision 3.

## Verified results

| Check | Result |
| --- | --- |
| Offline project checks | 171 pass |
| Optional actual PDF parser checks | 4 pass; pypdf 6.10.0 |
| Ruff | Pass |
| Actual disposable subprocess STDIO sessions | 2: original corpus and candidate reference archive |
| MCP catalog | Exactly five fixed read-only tools |
| Exact source citations checked over STDIO | 8 |
| Forbidden operational/mutation tool requests | Five rejected in each session |
| Invalid Boolean pagination argument | Rejected in each session |
| Seed integrity | 10,291 artifacts, 984 original responses, 5,261 sources verified |
| Candidate archive integrity | 28 artifacts, 8 original responses, 8 sources verified |
| Original corpus and lesson ledger | Manifest and ledger hashes unchanged |
| Pending proposal release | Refused |

[Offline tests](offline-tests.txt) include real in-memory JSON-RPC, REST/MCP parity,
Unicode spans, bounds, generic errors, tampering, candidate labels and hostile-CWD
configuration/import sentinels. The installed MCP package transitively re-exports
FastMCP, but this adapter never constructs its settings or reads `.env`. Other
checks cover read-only SQLite, missing stores, atomic revision selection, model
versus human review, stale packets, exact release acknowledgments, preserved
counter-context/predicted outcomes and structurally invalid bundles even when
their internal fingerprints are self-consistent. A disposable fixture also covers
the explicit human-review CLI followed by a separate release command; neither
command accepted or released either actual private proposal.

[Live-data acceptance](live-acceptance.json) records the two actual STDIO sessions,
source and ledger integrity, and the pending decision. Sessions ended when their
temporary test clients closed. No persistent daemon, MCP client configuration,
operational index, account connection or provider changed. The corpus pipeline
made zero model calls and performed no training.

Runtime: CPython 3.14.8 arm64, SQLite 3.54.0, MCP SDK 1.26.0. PDF regressions used
the existing bundled CPython 3.12.14 arm64 runtime and pypdf 6.10.0. No environment
was replaced. Hosted workflow coverage was extended but not run here; the full
daemon-changing CI gate was not run for this standalone feature.

The independent review caught and fixed a bundle-inspection issue: internally
matching hashes alone had admitted invalid version-1 event structures. Inspection
now checks event, record, outcome, citation and review structure before succeeding.
These checks do not authenticate a human identity, establish source truth, verify
external rights or independently attest the complete historical ledger.

## Use and remaining work

See [Free Man architecture and commands](../../../../docs/free-man.md) and the
[corpus guide](../../../../docs/lessons-corpus.md). Use an explicitly selected
corpus and fresh private artifact paths. [Fingerprints](fingerprints.json) bind
the source generations, private review artifacts and machine-readable receipts.

A same-account Python process retains that account's filesystem/network powers.
The fixed tool interface does not provide enforced OS isolation. Before running
untrusted research code, a restricted account/container, mount policy, resource
limits and enforced network policy remain required engineering work.

The next research step is a frozen comparison of reasoning with and without
selected source evidence, keeping exact prompts, model identity, citations,
uncertainty, counterexamples and human outcome review. Actual lesson transfer
remains blocked by the user's pending decisions and a separate exact release.
No automatic operational memory import, prompt injection, training or claims of
improved reasoning are part of this acceptance.
