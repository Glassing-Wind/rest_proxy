# Bounded compiled-query cache fix — 2026-10-21

Fixed and installed locally against base fork source `6fcead43fc13b0049481ea5b5c491e02eab4ac68`. The identical tested query.rs is now published as `d10c656bce9e5ac9e8c282230b7ea83295e7ab12` in [fork draft PR #3](https://github.com/Zmaroo/tree-sitter-language-pack/pull/3). This change pins all three rest_proxy requirements files to that commit.

## Change

`crates/ts-pack-core/src/query.rs` now uses insertion-order eviction in both compiled-query owners: 32 shared shards of at most 8 entries (256 total), and at most 32 entries per thread. Keys over 64 KiB (language + query UTF-8 bytes) bypass caching while queries still execute normally. A concurrent compiler reuses the canonical shared Arc before populating the thread-local cache. Caller-owned PreparedQuery handles survive eviction. Grammar libraries remain loaded for pointer validity.

These are entry limits, not a strict process-memory budget: compiled query sizes differ, callers may retain handles, allocator pages can remain resident, and each live thread has its own bounded cache. No API/schema or required dependency changes.

## Evidence

- Rust unit regressions: 2 passed, covering eviction, release of ownership, duplicate reuse, large-key bypass and disabled capacity.
- Explicit cached-Python Rust regression: 1 passed, executing 6,000 unique patterns, checking every shared shard and the actual thread-local entry count, querying an evicted held handle, confirming oversized compiled queries are unretained and confirming thread-local owners release at thread exit.
- Python contract/facts suite: 9 passed in isolated candidate imports and 9 passed again after installation.
- Fresh original-build reproduction: ~10.95 MiB retained after 6,000 patterns and replay/tree deletion. Two candidate runs retained ~2.00 MiB and ~0.56 MiB respectively; allocation/fragmentation affects RSS, so bounded cache assertions are the stronger ownership evidence.
- Longer candidate probe: 600 warmup + 12,000 distinct patterns, 160 KiB additional RSS; descriptor and thread counts unchanged. The installed-runtime repeat grew 1.625 MiB after warmup, demonstrating RSS allocator variation even with the same native binary. Separate fixed-query, unique-source processing, facts, payload and thread-turnover probes passed. Raw JSON is retained beside this report.
- Installed-runtime extension: 60,000 distinct patterns after 600 warmup calls; total RSS delta 1,982,464 bytes, with 360,448 bytes additional RSS from call 12,000 to 60,000. File descriptors stayed 3 and threads stayed 1.
- Offline release wheel build passed; rustfmt and patch/diff checks passed.
- Strict Clippy fails with 55 existing diagnostics in the pinned source. Baseline and patched builds have identical error-message sets; no diagnostic names the changed query.rs. This is not a passing repository-wide lint gate.

## Local installation and rollback

The tested wheel is retained at `.runtime/query-cache-fix-20261021/tree_sitter_language_pack-1.20.0-cp310-abi3-macosx_11_0_arm64.whl`. It was installed with `.venv/bin/python -m pip install --no-index --no-deps --force-reinstall <wheel>`. The installed native extension hash matches the tested candidate; receipt.json records both wheel hashes and the installed native hash. The receipt distinguishes historical installation from current publication. Existing services were not restarted; already-running processes continue using their loaded extension until restart.

Rollback: reinstall `.runtime/reliable-indexing/wheels/tree_sitter_language_pack-1.20.0-cp310-abi3-macosx_11_0_arm64.whl` with the same pip flags. This preserves the earlier wheel untouched.

## Reproduction and distribution

Use a checkout of published fork commit `d10c656bce9e5ac9e8c282230b7ea83295e7ab12`. Build offline using `TSLP_OFFLINE=1 maturin build --offline --release --manifest-path crates/ts-pack-python/Cargo.toml --out <wheel-dir>` (requires the existing Rust/Python dependency cache). Run `TSLP_OFFLINE=1 cargo test --offline -p tree-sitter-language-pack --lib query::tests::bounded`. The explicit Python-grammar test is `cargo test --offline -p tree-sitter-language-pack --lib query::tests::compiled_cache_bounds_with_python -- --ignored --test-threads=1`, with `TSLP_OFFLINE=1` and `TREE_SITTER_LANGUAGE_PACK_CACHE_DIR` pointing at a disposable base containing the already-cached Python grammar in `tree-sitter-language-pack/v1.20.0/libs`.

The source fix is published on the fork review branch, and requirements.txt, requirements-ci.txt and requirements-embedded.txt now pin the fixed commit. Both pull requests remain draft; hosted acceptance is pending. The previously installed wheel was built from the same query.rs; it was not rebuilt or reinstalled during this distribution correction. The original checkout is preserved and still has its old requirements until this change is integrated. Service restart remains blocked by sandbox permissions. Do not treat a running launchd label or an inaccessible loopback health endpoint as proof of restart or health.


## Review branch verification

The isolated rest_proxy checkout has the same base Git tree as hosted PR #13 head `278974ed9f95e77c38096c5bb5d2d3d8c15dd956` (`bb921dac86b1100ea1e2afd98fa492664706fa09`). No original-checkout, production database, model, or service configuration change was made. Raw probe receipts are historical October 21 executions, not new hosted measurements.
