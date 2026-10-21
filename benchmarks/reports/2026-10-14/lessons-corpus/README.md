# Lessons corpus acceptance

The authorized seed is [21st Century Progressivism](https://21stcenturyprogressivism.com/).
Acquisition finished October 14, 2026, at 23:17:18 UTC. Representation and live-data
acceptance finished October 15. Source bodies and lesson records remain in the
private local archive outside Git. This receipt contains counts and integrity
results, not those bodies or any private case material.

## Result

| Item | Verified result |
| --- | --- |
| Published posts / pages | 116 / 7 |
| Derived whitespace words in post/page content | 184,557 |
| Other public WordPress objects | 6 comments, 104 media records, 23 categories, 743 tags, 4 series, 2 authors |
| Response bodies acquired | 984; 100,704,776 transferred body bytes, including repeated responses |
| Represented source records | 5,261 |
| Structural syntax nodes | 969,969 |
| Reference occurrences | 97,514 |
| External URL targets inventoried | 477; bodies not acquired |
| Same-site PDFs | 2 originals, 60 extracted pages; no empty extracted pages |
| Representation processing failures | 0 |
| Integrity verification | 10,291 artifact files and 984 original responses checked |
| Proposed lessons | 2; citation/revision integrity survives reopen; human review pending |

Reference counts include HTML attributes, metadata, repeated page templates, PDF
annotation URIs and literal URLs. They are not counts of verified historical facts,
independent relationships or distinct works. Images, audio/video, OCR and external
reference bodies are outside this acquisition. PDF extraction preserves the original
bytes separately and supplies page provenance; it is not complete visual transcription.

The two proposed lessons concern help without humiliation and retaining an author's
intentions/context. They are attributed assistant interpretations of exact source
passages, with predictions marked untested and counterexample review pending. The
assistant's exact runtime model identity was unavailable and is recorded that way.
No human review, model adaptation or measured improvement is claimed.

## Acquisition gaps and assessment

The raw capture's `complete_within_declared_scope` is **false**. Its representation
is complete for the acquired inputs. These are separate results:

1. Eight legacy internal URLs returned 404. They remain unresolved reference
   targets; related current articles do not establish equivalence. The exact URLs
   appear in [live-acceptance.json](live-acceptance.json).
2. Both media passes returned the same 104 distinct objects while advertising
   108. Page one returned 96, page two 8. The preserved bodies and hashes match
   between passes; this is a stable server count/visibility inconsistency, not
   observed client truncation or media drift. Four advertised records remain
   unexplained.
3. Full-object page hashing detected a change to Contact page 6212. Inspection
   found only a generated hidden Contact Form 7 `_wpcf7_ak_js` value changed.
   No authored wording change was observed. The full-hash warning is retained.

The long-running acquisition process loaded an earlier implementation before the
code commit. Later fixes distinguish attempted HTML from successfully acquired
HTML and discover caption-only embedded PDFs. For this capture, independent
successful-response/decoding audit identified the eight failed HTML targets even
though its earlier pending list was empty. Every same-site PDF target in the final
reference inventory points to one of the two acquired originals. No source snapshot
or original coverage warning was rewritten to improve the result.

## Verification

- [103 offline checks](offline-tests.txt): acquisition bounds, source/token/span
  fidelity, Unicode, typed provenance, tamper rejection, query filtering, immutable
  reviews/outcomes, reopen/CAS, REST authentication, Host/Origin/peer checks, bounded
  projections and CLI binding.
- [4 actual PDF parser checks](pdf-tests.txt), using `pypdf/6.10.0`, including
  indirect annotations, encrypted-input refusal and generic MIME handling.
- [Ruff](ruff.txt): passes for the feature's Python modules and tests.
- [Live-data acceptance](live-acceptance.json): full artifact/original hashes;
  source-token round trips; exact search citations; two proposed lesson revisions
  reopened; five authenticated REST reads plus unauthenticated/browser rejection.
  REST checks used in-process ASGI, with no listener or persistent service started.

Project runtime: CPython 3.14.8, native arm64, SQLite 3.54.0. The explicitly selected
bundled PDF worker used CPython 3.12.14, native arm64, and pypdf 6.10.0. No project
environment was replaced. The dedicated hosted CI workflow was added but has not
been run by this local verification. Full daemon-changing CI was not needed for
this standalone feature and was not run.

Reproduce from the repository root, using the actual private capture path:

```sh
.venv/bin/python -m scripts.lessons_corpus build \
  --capture /absolute/private/path/site-capture \
  --pdf-python /absolute/path/to/python-with-pypdf
.venv/bin/python -m scripts.lessons_corpus verify \
  --corpus /absolute/private/path/site-capture/representation
.venv/bin/python -m scripts.lessons_corpus search \
  --corpus /absolute/private/path/site-capture/representation \
  --query wisdom --source-kind wordpress_post_content_html --limit 3
```

Build refuses an existing representation; verification/search can be repeated.
The capture and representation fingerprints are in [fingerprints.json](fingerprints.json).
Full source words remain in the local originals/ASTs; the acquisition's permission
is user-attested archival use. Redistribution and training rights are not established
by that permission record.

These checks establish inspectable storage and retrieval contracts. They do not
establish moral understanding, training effectiveness, retrieval superiority,
end-to-end outcome improvement or production readiness. The next work is recovery
of unresolved references, review of the first proposals, and evaluated MCP/model
integration using explicit source editions and rights.
