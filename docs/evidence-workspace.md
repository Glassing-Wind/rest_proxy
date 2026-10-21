# Local evidence workspace

This opt-in prototype supplies one complete workflow: import a source package,
inspect cited originals, review a relationship with a reason, export reviewed
findings, and restore originals plus review history. It supports repository
investigations and other explicitly prepared evidence collections with the same
source/citation/review contracts.

The workspace does not acquire email, Messages, Photos or Coinbase records. It
does not load private cases automatically. Acquisition adapters and GraphRAG query
integration remain separate work; this first surface uses a dedicated local case
store. Review labels do not authenticate a source, person, wallet or payment.

## Run

Use the existing environment. Text-only review needs the core dependencies. PNG
and JPEG imports additionally need Pillow; the optional `evidence` installation
extra pins the same version as the full profile (`pip install '.[evidence]'`). Do
not reinstall a working environment just to start the viewer.

Choose a new absolute, canonical state path outside the checkout. The final
directory must belong to your user with mode 700. Existing state files require
mode 600. Symlink components and hardlinked state files are refused. On macOS,
use `/private/tmp` rather than its `/tmp` symlink for disposable acceptance.

```sh
.venv/bin/python -m scripts.evidence_workspace \
  --state "$HOME/.local/share/rest_proxy/evidence-workspace" --demo
```

`--demo` explicitly imports fictional records; omit it for an empty workspace.
The listener binds only `127.0.0.1`; an unused port is chosen by default. `--port`
selects a fixed port. This command starts one standalone process and does not
restart the MCP daemon, inference proxy, models or indexing services.

Open the URL in the state directory's private `launch.json`. It conveys a random
per-launch capability in a fragment; the page removes the fragment and keeps the
capability in memory. Reload/disconnect clears access; reopen the launch URL or
enter its token in the local access form. Tokens are not printed in server logs
or included in case backups. Stop the viewer with Ctrl-C. It removes only its own
launch file. Concurrent launches sharing a directory are refused. After a crash,
confirm the viewer has stopped before manually removing its stale launch file.

The UI supports source selection, cited text lines, image regions, relationship
search/filtering, accepted/rejected/amended reviews and complete review history.
Each mutation checks the current case revision. A conflict reloads evidence and
requires inspection before another submission. Coverage declarations remain
importer-reported; unavailable and unlisted expected sources appear separately.

## Import contract

Import one explicitly selected JSON file. Unknown fields, duplicate JSON keys,
invalid references, incompatible hashes and malformed citations are rejected
before any case is committed. This minimal package illustrates the schema:

```json
{
  "schema_version": 1,
  "case_id": "example",
  "title": "Importer source inspection",
  "coverage": {"status": "partial", "notes": "One supplied source excerpt."},
  "sources": [{
    "id": "source-1", "label": "Importer excerpt", "kind": "provided-code",
    "media_type": "text/plain", "text": "return store.write(rows)\n",
    "original_reference": "importer.py", "source_revision": "provided-snapshot"
  }],
  "entities": [
    {"id": "importer", "label": "Importer", "kind": "code-component"},
    {"id": "store", "label": "Store", "kind": "code-component"}
  ],
  "relationships": [{
    "id": "write-reference", "source": "importer", "target": "store",
    "predicate": "references-write", "statement": "The source references store.write.",
    "evidence_status": "observation",
    "citations": [{"source_id": "source-1", "start_line": 1, "end_line": 1,
                   "quote": "store.write"}]
  }]
}
```

Source media: UTF-8 `text/plain`, `text/csv`, `application/json`, `image/png`, or
`image/jpeg`. `content_base64` preserves exact acquired bytes instead of the
`text` field; they are mutually exclusive. Optional SHA256 must match supplied
bytes. `observed_at`, when supplied, requires an ISO timestamp with explicit
timezone. `original_reference` is a descriptive label, never a path to open or
a URL to fetch. An unavailable source has no content and cannot support a citation.

Text citations use inclusive one-based lines and at most 100 lines. A supplied
quote must occur in those lines. Images use `bbox` with normalized top-left
`x`, `y`, `width`, `height` in 0..1. Pillow verifies format/pixels; region checking
does not validate an image transcription. Image quotes remain explicitly
unverified. Animated images, HTML, SVG and HEIC are unsupported in this first
import contract; preserve unsupported originals outside this store rather than
silently converting them.

Evidence type is immutable: `observation`, `source_claim`, or `inference`.
Accepted/amended/rejected is a separate human review state with a required reason
and self-reported reviewer label. An amendment changes the effective statement;
accepting it later retains that amendment. Originals and earlier decisions remain
available in history and the recovery archive.

## Freshness and recovery

A hash proves consistency with preserved bytes. To compare a current copy,
explicitly select its file or record a missing marker. The latest operator check
shows timestamp, revision and compared hash. These are historical supplied-copy
checks; the workspace does not independently watch files or external accounts.

The Markdown report contains current accepted/amended statements, reasons,
source references, coverage gaps and dated checks. It excludes original byte
payloads and retired review statements. The recovery ZIP contains all originals,
the immutable baseline, complete review/check history, a canonical report and a
hash/size manifest. Both exports can contain sensitive information; backups are
unencrypted. Review reports before sharing and store archives privately.

Restore validates the entire packet, citations, hashes, event order and canonical
report before one atomic commit. It reads bounded ZIP members without filesystem
extraction. Only stored/deflated entries are accepted. Existing case IDs are never
overwritten: supply a new ID to restore alongside the original. A blank ID retains
the archive's ID and conflicts if it exists. Renamed restores retain origin identity.

Limits: 32 cases, 64 sources, 128 entities and 256 relationships per case; 16
citations per relationship; 256 KiB per text source; 8 MiB per original and 32 MiB
originals per case; 16 million pixels per image; 48 MiB import request; 56 MiB
recovery ZIP. History permits at most 1024 events and must keep aggregate metadata
and rendered report within their separate 8 MiB recovery budgets. A rejected
mutation leaves the previous revision and recovery export intact.

## Trust boundary and validation

Evidence APIs require the per-launch capability, actual loopback connection,
exact Host and same-origin Origin when present. No token cookies/query strings,
ambient credentials, CORS, remote acquisition or model calls are used. Static
pages contain no case data. CSP, no-store and safe text rendering constrain the
browser surface. Sources are displayed as data, never executed as instructions.

This boundary is a local single-user prototype. It is not shared-service identity,
authorization, encryption at rest, an OS sandbox or a tamper-proof forensic chain
of custody. A process with the same user's filesystem access can modify state or
read the launch file. Keep that limitation explicit when discussing an IC3 report
or commercial investigation product.

Run focused, offline acceptance without production services:

```sh
.venv/bin/python test_evidence_workspace.py
.venv/bin/python test_evidence_workspace_http.py
.venv/bin/python test_evidence_workspace_launch.py
node scripts/check_evidence_workspace_ui.cjs
.venv/bin/python test_distribution.py
```

The dated [acceptance record](../benchmarks/reports/2026-10-14/evidence-workspace/README.md)
uses synthetic temporary state. Passing contracts establish bounded functionality
and recovery, not better investigation outcomes, identity attribution or commercial
readiness.
