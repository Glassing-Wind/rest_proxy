"""Bounded source evidence shared by the explicitly selected REST and MCP readers.

No acquisition, ledger, model, .env, operational configuration or service runtime
is imported. Same-account processes retain OS privileges: this is data selection,
not an OS sandbox. Consumers must project source words as attributed evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from graphrag_core.lessons_corpus.corpus import Corpus, interpretation_status, _safe_file, sha256


MAX_PAGE = 100
MAX_SLICE = 4096
MAX_URL = 4096
_STATUSES = frozenset({
    'not_fetchable_or_unresolved', 'excluded_admin_or_login', 'acquired_original',
    'same_site_unacquired', 'external_unacquired',
})
_SOURCE_FIELDS = (
    'source_id', 'source_kind', 'url', 'source_sha256', 'text_sha256', 'parser_version',
    'text_characters', 'whitespace_words', 'node_count', 'reference_occurrences',
)
_REFERENCE_FIELDS = (
    'id', 'source_id', 'source_sha256', 'node_id', 'attribute', 'source_span',
    'value_span', 'text_span', 'target_status', 'target_reason', 'acquisition_status', 'page',
)


class InvalidQuery(ValueError):
    """A request does not match the bounded public query contract."""


class MissingSource(LookupError):
    """The requested source identifier is absent from this selected corpus."""


def _page(items: list, offset: int, limit: int, name: str) -> dict:
    end = offset + limit
    return {name: items[offset:end], 'offset': offset, 'limit': limit, 'total': len(items),
            'next_offset': end if end < len(items) else None}


def _source_identity(source: dict) -> None:
    if type(source) is not dict:
        raise ValueError('Invalid checked source identity')
    for name, budget in (('source_id', 2048), ('source_kind', 128), ('parser_version', 128)):
        value = source.get(name)
        if type(value) is not str or not value.strip() or len(value) > budget or '\x00' in value:
            raise ValueError('Invalid checked source identity')


def _url_projection(value: object) -> dict:
    if type(value) is not str:
        raise ValueError('Invalid checked source URL')
    return {'url': value[:MAX_URL], 'url_truncated': len(value) > MAX_URL}


class CorpusReads:
    """Read one explicit canonical corpus; no defaults, writes or target fetches."""

    def __init__(self, corpus_path: str):
        if type(corpus_path) is not str:
            raise ValueError('Require an explicit absolute corpus path without symlink components')
        root = Path(corpus_path)
        if not root.is_absolute() or '..' in root.parts or root.resolve() != root:
            raise ValueError('Require an explicit absolute corpus path without symlink components')
        self.corpus = Corpus(root)
        self.manifest_raw = _safe_file(root, 'manifest.json').read_bytes()
        self.manifest_sha256 = sha256(self.manifest_raw)
        if json.loads(self.manifest_raw) != self.corpus.manifest:
            raise ValueError('Corpus manifest changed while opening')

    def checked_catalog(self):
        if _safe_file(self.corpus.root, 'manifest.json').read_bytes() != self.manifest_raw:
            raise ValueError('Corpus manifest changed since opening')
        self.corpus._checked('sources.jsonl')

    def source_row(self, source_id: str) -> dict:
        self.checked_catalog()
        if source_id not in self.corpus.sources:
            raise MissingSource
        row = self.corpus.sources[source_id]
        _source_identity(row)
        if row['source_id'] != source_id:
            raise ValueError('Checked source identifier does not match its catalog key')
        return row

    def catalog(self, offset: int, limit: int) -> dict:
        self.checked_catalog()
        rows = []
        for source_id, source in sorted(self.corpus.sources.items()):
            _source_identity(source)
            if source['source_id'] != source_id:
                raise ValueError('Checked source identifier does not match its catalog key')
            row = {name: source[name] for name in _SOURCE_FIELDS if name != 'url'}
            rows.append({**row, **_url_projection(source['url']),
                         'interpretation_status': interpretation_status(source)})
        return {**_page(rows, offset, limit, 'sources'),
                'verification': 'checked_catalog; source_bytes_checked_on_read'}

    def excerpt(self, source_id: str, start: int, requested_end: int | None) -> dict:
        self.source_row(source_id)
        source = self.corpus.source(source_id)
        _source_identity(source)
        if source['source_id'] != source_id:
            raise ValueError('Checked source identifier does not match its requested identifier')
        end = min(len(source['text']), start + 512) if requested_end is None else requested_end
        if not 0 <= start <= end <= len(source['text']) or end - start > MAX_SLICE:
            raise InvalidQuery
        wording = source['text'][start:end]
        citation = None if start == end else {
            'source_id': source_id, 'source_sha256': source['source_sha256'],
            'text_sha256': source['text_sha256'], 'start_char': start, 'end_char': end, 'quote': wording,
        }
        return {'source_id': source_id, 'source_kind': source['source_kind'], **_url_projection(source['url']),
                'parser_version': source['parser_version'], 'text': wording, 'citation': citation,
                'start_char': start, 'end_char': end, 'total_characters': len(source['text']),
                'next_start_char': end if end < len(source['text']) else None,
                'interpretation_status': interpretation_status(source)}

    def references(self, offset: int, limit: int, source_id: str | None, status: str | None) -> dict:
        self.checked_catalog()
        if source_id is not None:
            self.source_row(source_id)
        selected = []
        for line in self.corpus._checked('references.jsonl').decode('utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (not isinstance(row, dict) or row.get('source_id') not in self.corpus.sources
                    or row.get('source_sha256') != self.corpus.sources[row['source_id']]['source_sha256']
                    or row.get('acquisition_status') not in _STATUSES):
                raise ValueError('Invalid checked reference contract')
            referenced_source = self.corpus.sources[row['source_id']]
            _source_identity(referenced_source)
            if referenced_source['source_id'] != row['source_id']:
                raise ValueError('Checked reference source identifier differs from the catalog')
            if source_id is not None and row['source_id'] != source_id:
                continue
            if status is not None and row['acquisition_status'] != status:
                continue
            item = {key: row[key] for key in _REFERENCE_FIELDS if key in row}
            for name, budget in (('label', 512), ('resolved_url', 4096)):
                value = row.get(name)
                if value is not None and not isinstance(value, str):
                    raise ValueError('Invalid checked reference value')
                item[name] = value[:budget] if value is not None else None
                item[name + '_truncated'] = value is not None and len(value) > budget
            selected.append(item)
        return {**_page(selected, offset, limit, 'references'),
                'verification': 'checked_reference_artifact; target_contents_not_acquired_by_listing'}

    def search(self, query: str, limit: int, source_kind: str | None) -> dict:
        self.checked_catalog()
        results = []
        for match in self.corpus.search(query, limit=limit, source_kind=source_kind):
            row = self.source_row(match['source_id'])
            if match['source_kind'] != row['source_kind'] or match['url'] != row['url']:
                raise ValueError('Search source metadata differs from the checked catalog')
            results.append({
                'source_id': row['source_id'], 'source_kind': row['source_kind'],
                **_url_projection(match['url']), 'citation': match['citation'],
                'interpretation_status': interpretation_status(row),
            })
        return {'results': results}

    def status(self) -> dict:
        self.checked_catalog()
        coverage = json.loads(self.corpus._checked('coverage.json'))
        complete = coverage.get('representation_complete')
        captured = coverage.get('capture_coverage', {}).get('complete_within_declared_scope')
        if type(complete) is not bool or type(captured) is not bool:
            raise ValueError('Invalid checked coverage status')
        return {
            'surface': 'free-man-source-corpus', 'read_only': True,
            'manifest_sha256': self.manifest_sha256,
            'source_count': len(self.corpus.sources),
            'representation_complete': complete, 'capture_complete_within_declared_scope': captured,
            'verification': 'checked_catalog_and_coverage; original_bytes_checked_on_source_read',
            'interpretation_status': 'attributed_source_evidence; not verified claim or training',
        }
