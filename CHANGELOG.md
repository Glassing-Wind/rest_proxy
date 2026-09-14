# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub Actions CI workflow for gated lint and regression checks
- GitHub Actions security workflow for secret scanning and dependency auditing
- Local CI runner and dependency audit scripts
- Live `tree_sitter_language_pack` contract test covering the supported integration surface
- Portable project metadata, an MIT license file, focused operating guides, and
  a native-vs-MCP agent-tooling comparison harness

### Changed

- Documentation and indexing chunking now adapt to both `chunk_overlap` and `_chunk_overlap`
  `tree_sitter_language_pack` API shapes
- Graph regression and CI shell scripts now resolve the intended Python runtime before running
- Security remediation upgraded `aiohttp`, `cryptography`, `curl_cffi`, `Pygments`,
  `transformers`, and `sentence-transformers`, and replaced the non-portable
  local `packaging @ file://...` requirement with a standard version pin
- The sample environment now uses safe opt-in defaults and a recognized memory mode
- CI linting now covers all production packages and operational scripts
- Dependency auditing now blocks new, non-baselined advisories instead of being
  globally advisory; existing upgrade debt is recorded explicitly
- Fixed the graph-backed `get_test_coverage_for` fallback to execute through its
  open Neo4j session

## [0.1.0] - 2026-04-12

### Added

- Initial changelog and repository version marker for release automation
