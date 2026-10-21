"""Read-only reference occurrence inventory for explicit, separately authorized recovery.

Targets are grouped by exact observed URL spelling with its fragment removed.
Neither an acquisition label nor URL grouping proves identity, archived equivalence,
source truth, permission to copy another site, or successful reference recovery.
"""
from __future__ import annotations

from collections import Counter
import json
import re
from urllib.parse import urlsplit

from graphrag_core.lessons_corpus.ast import _host_status
from graphrag_core.lessons_corpus.corpus import _safe_file, sha256


SCHEMA = 'lessons-reference-frontier/1'
MAX_INDEX_BYTES = 64 * 1024 * 1024
MAX_DISPLAY_URL = 4096
MAX_OFFSET = 1000000
MAX_SAMPLES = 5
AUTHORED_KINDS = frozenset({'wordpress_post_content_html', 'wordpress_page_content_html'})
TARGET_STATUSES = frozenset({'http', 'non_fetchable', 'unsafe', 'malformed', 'unsupported_compound'})
ACQUISITION_STATUSES = frozenset({'not_fetchable_or_unresolved', 'excluded_admin_or_login',
                                 'acquired_original', 'same_site_unacquired', 'external_unacquired'})
REFERENCE_FIELDS = frozenset({
    'id', 'node_id', 'tag', 'attribute', 'raw_attribute_value', 'source_span', 'value_span',
    'scope', 'label', 'label_spans', 'text_span', 'resolved_url', 'target_status', 'target_reason',
    'source_id', 'source_sha256', 'acquisition_status', 'node_ids', 'observed_url', 'raw_literal',
    'trailing_punctuation', 'boundary_policy', 'source_spans', 'source_segments', 'quote',
    'provenance_scope', 'provenance_limitations', 'json_literal', 'decoded_value', 'page',
})
REQUIRED_REFERENCE_FIELDS = frozenset({'id', 'source_id', 'source_sha256', 'target_status',
                                      'acquisition_status', 'resolved_url'})
HASH = re.compile(r'[0-9a-f]{64}\Z')


def _json(raw: bytes | str):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate frontier artifact JSON key')
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError('Frontier artifacts require finite JSON')

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid frontier artifact JSON') from error


def _label(value, maximum: int, name: str) -> str:
    if type(value) is not str or not value.strip() or '\0' in value or len(value) > maximum:
        raise ValueError('Invalid reference ' + name)
    return value


def _checked(corpus, name: str) -> bytes:
    if _safe_file(corpus.root, name).stat().st_size > MAX_INDEX_BYTES:
        raise ValueError('Frontier artifact exceeds its 64 MiB input budget')
    raw = corpus._checked(name)
    if len(raw) > MAX_INDEX_BYTES:
        raise ValueError('Frontier artifact exceeds its 64 MiB input budget')
    return raw


def _rows(raw: bytes):
    for line in raw.splitlines():
        if line.strip():
            value = _json(line)
            if type(value) is not dict:
                raise ValueError('Frontier artifact rows must be objects')
            yield value


def _manifest(corpus) -> tuple[str, str]:
    path = _safe_file(corpus.root, 'manifest.json')
    if path.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError('Frontier manifest exceeds its input budget')
    raw = path.read_bytes()
    manifest = _json(raw)
    if manifest != corpus.manifest:
        raise ValueError('Corpus manifest changed since opening')
    seed = manifest.get('capture_inventory', {}).get('seed_url')
    if type(seed) is not str:
        raise ValueError('Frontier requires the captured seed URL')
    try:
        parsed = urlsplit(seed)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('Invalid seed URL')
        host = parsed.hostname.lower()
    except ValueError as error:
        raise ValueError('Frontier requires a valid captured seed URL') from error
    return sha256(raw), host


def _catalog(corpus) -> dict:
    result = {}
    for row in _rows(_checked(corpus, 'sources.jsonl')):
        identifier = _label(row.get('source_id'), 2048, 'source identifier')
        _label(row.get('source_kind'), 128, 'source kind')
        digest = row.get('source_sha256')
        if type(digest) is not str or not HASH.fullmatch(digest):
            raise ValueError('Invalid catalog source hash')
        if identifier in result:
            raise ValueError('Duplicate catalog source identifier')
        result[identifier] = row
    if result != corpus.sources:
        raise ValueError('Corpus source catalog changed since opening')
    return result


def _http_url(value: str) -> tuple[str, str]:
    if (not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                         for character in value) or '\\' in value
            or re.search(r'%(?![0-9a-fA-F]{2})', value)):
        raise ValueError('Malformed HTTP reference target')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError('Invalid HTTP target')
        parsed.port
        if _host_status(parsed.hostname)[0] is not None:
            raise ValueError('HTTP target was classified inconsistently')
    except ValueError as error:
        raise ValueError('Malformed HTTP reference target') from error
    # Removing the fragment by slicing preserves case, empty query punctuation
    # and every other character. URL reconstruction can normalize those details.
    return value.split('#', 1)[0], parsed.hostname.lower()


def _reference(row: dict, catalog: dict) -> tuple[str, str, tuple[str, str] | None]:
    if not REQUIRED_REFERENCE_FIELDS <= row.keys() or row.keys() - REFERENCE_FIELDS:
        raise ValueError('Reference fields do not match the frontier contract')
    source_id = _label(row['source_id'], 2048, 'source identifier')
    reference_id = _label(row['id'], 512, 'occurrence identifier')
    if source_id not in catalog:
        raise ValueError('Reference source is outside the checked catalog')
    if row['source_sha256'] != catalog[source_id]['source_sha256']:
        raise ValueError('Reference source hash does not match the checked catalog')
    if type(row['target_status']) is not str or row['target_status'] not in TARGET_STATUSES:
        raise ValueError('Unknown reference target status')
    status = row['acquisition_status']
    if type(status) is not str or status not in ACQUISITION_STATUSES:
        raise ValueError('Unknown reference acquisition status')
    target = row['resolved_url']
    if target is not None and type(target) is not str:
        raise ValueError('Reference target must be text or null')
    if row['target_status'] == 'http':
        if type(target) is not str or status == 'not_fetchable_or_unresolved':
            raise ValueError('HTTP reference target/status is inconsistent')
        observed_target = _http_url(target)
    elif status != 'not_fetchable_or_unresolved':
        raise ValueError('Non-HTTP reference acquisition status is inconsistent')
    else:
        observed_target = None
    return source_id, reference_id, observed_target


def _collect(corpus, scope: str) -> tuple[list[dict], str, dict]:
    if type(scope) is not str or scope not in {'authored', 'all'}:
        raise ValueError('Reference scope must be authored or all')
    manifest_hash, seed_host = _manifest(corpus)
    catalog = _catalog(corpus)
    selected = {identifier for identifier, row in catalog.items()
                if scope == 'all' or row['source_kind'] in AUTHORED_KINDS}
    groups, seen = {}, set()
    total, scoped, eligible = 0, 0, 0
    for row in _rows(_checked(corpus, 'references.jsonl')):
        source_id, reference_id, observed_target = _reference(row, catalog)
        occurrence_key = (source_id, reference_id)
        if occurrence_key in seen:
            raise ValueError('Duplicate source/reference occurrence identifier')
        seen.add(occurrence_key)
        total += 1
        if source_id not in selected:
            continue
        scoped += 1
        if row['target_status'] != 'http':
            continue
        url, host = observed_target
        eligible += 1
        bucket = groups.setdefault(url, {'url': url, 'origin': 'same-site' if host == seed_host else 'external',
                                        'statuses': Counter(), 'source_ids': set(), 'samples': []})
        bucket['statuses'][row['acquisition_status']] += 1
        bucket['source_ids'].add(source_id)
        sample = {'source_id': source_id, 'reference_id': reference_id,
                  'source_sha256': row['source_sha256']}
        bucket['samples'].append(sample)
        bucket['samples'].sort(key=lambda value: (value['source_id'], value['reference_id'], value['source_sha256']))
        del bucket['samples'][MAX_SAMPLES:]
    targets = []
    for url, bucket in sorted(groups.items()):
        occurrences = sum(bucket['statuses'].values())
        targets.append({'target_id': sha256(url.encode('utf-8')), 'url': url,
                        'origin': bucket['origin'], 'occurrence_count': occurrences,
                        'source_count': len(bucket['source_ids']),
                        'acquisition_status_counts': dict(sorted(bucket['statuses'].items())),
                        'acquired_original_occurrences': bucket['statuses'].get('acquired_original', 0),
                        'occurrence_samples': bucket['samples'],
                        'samples_truncated': occurrences > len(bucket['samples'])})
    totals = {'catalog_source_count': len(catalog), 'scope_source_count': len(selected),
              'catalog_reference_occurrences': total, 'scope_reference_occurrences': scoped,
              'http_reference_occurrences': eligible, 'non_http_reference_occurrences': scoped - eligible,
              'target_count': len(targets),
              'acquired_original_occurrences': sum(row['acquired_original_occurrences'] for row in targets)}
    return targets, manifest_hash, totals


def collect_targets(corpus, scope: str = 'authored') -> list[dict]:
    """Return full exact target URLs for a separately scoped recovery selection."""
    targets, _, _ = _collect(corpus, scope)
    return targets


def reference_frontier(corpus, scope: str = 'authored', origin: str = 'all',
                       offset: int = 0, limit: int = 50) -> dict:
    """Return a bounded, checked frontier page, including acquired occurrence labels."""
    if type(origin) is not str or origin not in {'all', 'same-site', 'external'}:
        raise ValueError('Reference origin must be all, same-site or external')
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError('Reference offset must be an integer from 0 through 1000000')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Reference limit must be an integer from 1 through 100')
    targets, parent_hash, totals = _collect(corpus, scope)
    filtered = [row for row in targets if origin == 'all' or row['origin'] == origin]
    page = [{**row, 'url': row['url'][:MAX_DISPLAY_URL],
             'url_truncated': len(row['url']) > MAX_DISPLAY_URL} for row in filtered[offset:offset + limit]]
    return {'schema': SCHEMA, 'parent_manifest_sha256': parent_hash, 'scope': scope, 'origin': origin,
            'totals': totals, 'filtered_totals': {'target_count': len(filtered),
                                                'occurrence_count': sum(row['occurrence_count'] for row in filtered)},
            'offset': offset, 'limit': limit, 'next_offset': offset + len(page) if offset + len(page) < len(filtered) else None,
            'targets': page,
            'meaning': 'Observed URL occurrences and acquisition labels; no recovery, identity or permission is established.'}
