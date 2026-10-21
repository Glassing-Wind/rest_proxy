"""Bounded James source import, attributed cross-references and edition-aware reads.

Only explicit local acquisition receipts are imported. No network, model or
operational store is used. Whole publisher archives remain originals; only James
and the James-outbound reference subset enter the searchable representation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from graphrag_core.lessons_corpus.corpus import Corpus, _write, canonical, sha256
from graphrag_core.lessons_corpus.scripture import parse_usfm_document, read_james_archive


SELECTION_VERSION = 'openbible-james-outbound/1'
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_MEMBER = 16 * 1024 * 1024
MAX_ROWS = 500000
EDITION_URLS = {
    'engwebp': 'https://ebible.org/Scriptures/engwebp_usfm.zip',
    'eng-asv': 'https://ebible.org/Scriptures/eng-asv_usfm.zip',
}
DATASET_URL = 'https://a.openbible.info/data/cross-references.zip'
EDITION_TITLES = {'engwebp': 'World English Bible, Protestant edition (engwebp)',
                  'eng-asv': 'American Standard Version (1901), eBible eng-asv'}
DATASET_RIGHTS = {
    'attribution': 'Cross-reference data by OpenBible.info; James-outbound rows selected without changing votes.',
    'source_url': 'https://www.openbible.info/labs/cross-references/',
    'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
    'interpretation': 'Attributed editorial/thematic links; votes are not truth, independence or confidence.',
}
_ADDRESS = r'[1-3]?[A-Za-z]+\.[1-9]\d{0,2}\.[1-9]\d{0,2}'
_REFERENCE = re.compile(_ADDRESS + '(?:-' + _ADDRESS + ')?')
_JAMES_VERSES = {f'JAS.{chapter}.{verse}' for chapter, count in enumerate((27, 26, 18, 17, 20), 1)
                 for verse in range(1, count + 1)}


def _complete_book(ast: dict) -> None:
    identifiers = [verse['id'] for verse in ast['verses']]
    if len(identifiers) != 108 or set(identifiers) != _JAMES_VERSES:
        raise ValueError('The James pilot requires all 108 expected verse markers in each edition')


def select_crossrefs(raw: bytes) -> dict:
    """Select original James rows with offsets into both original and subset text."""
    if type(raw) is not bytes or len(raw) > MAX_ARCHIVE:
        raise ValueError('Cross-reference archive exceeds its byte budget')
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError('Invalid cross-reference ZIP archive') from None
    with archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > 10 or len(names) != len(set(names)):
            raise ValueError('Cross-reference archive has too many or duplicate entries')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                raise ValueError('Unsafe cross-reference archive entry')
        members = [entry for entry in entries if entry.filename == 'cross_references.txt']
        if len(members) != 1 or members[0].file_size > MAX_MEMBER:
            raise ValueError('Require one bounded cross_references.txt member')
        with archive.open(members[0]) as stream:
            payload = stream.read(MAX_MEMBER + 1)
        if len(payload) > MAX_MEMBER:
            raise ValueError('Cross-reference member exceeds its byte budget')
    text = payload.decode('utf-8', errors='strict')
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith('From Verse\tTo Verse\tVotes\t#www.openbible.info CC-BY '):
        raise ValueError('Unsupported OpenBible header; attribution must be retained')
    if len(lines) - 1 > MAX_ROWS:
        raise ValueError('Cross-reference row budget exceeded')
    selected, records = [lines[0]], []
    original_offset = subset_offset = len(lines[0])
    for number, line in enumerate(lines[1:], 2):
        fields = line.rstrip('\r\n').split('\t')
        if (len(fields) != 3 or not all(_REFERENCE.fullmatch(value) for value in fields[:2])
                or not re.fullmatch(r'-?\d{1,9}', fields[2])):
            raise ValueError('Malformed cross-reference row')
        if fields[0].startswith('Jas.'):
            if '-' in fields[0]:
                raise ValueError('Source ranges are outside the James pilot contract')
            records.append({'id': f'openbible:line:{number}', 'from': fields[0], 'to': fields[1],
                            'votes': int(fields[2]), 'original_line': number,
                            'original_text_span': [original_offset, original_offset + len(line)],
                            'text_span': [subset_offset, subset_offset + len(line)], 'quote': line})
            selected.append(line)
            subset_offset += len(line)
        original_offset += len(line)
    if not records or len(records) > 10000:
        raise ValueError('Require one through 10000 James-outbound references')
    return {'member': members[0].filename, 'member_sha256': sha256(payload),
            'text': ''.join(selected), 'records': records, 'dataset_rows': len(lines) - 1,
            'header': lines[0].rstrip('\r\n')}


def scripture_source(acquisition: dict, raw: bytes) -> dict:
    """Derive one fixed-scope source and its provenance from checked archive bytes."""
    if acquisition.get('purpose') == 'scripture_usfm_archive':
        edition = acquisition.get('edition_id')
        if edition not in EDITION_URLS or acquisition.get('requested_url') != EDITION_URLS[edition]:
            raise ValueError('Unknown James edition acquisition')
        member, text = read_james_archive(raw)
        if member != f'89-JAS{edition}.usfm':
            raise ValueError('James archive member does not identify the selected edition')
        metadata = {'edition_id': edition, 'edition_title': EDITION_TITLES[edition],
                    'language': 'eng', 'book': 'JAS',
                    'versification': 'JAS marker numbering; cross-edition equivalence not established',
                    'rights_basis': 'Publisher-declared public domain',
                    'rights_url': f'https://ebible.org/{edition}/copyright.htm',
                    'source_url': EDITION_URLS[edition], 'archive_member': member,
                    'member_sha256': sha256(text.encode('utf-8')),
                    'transformation': 'UTF-8 USFM member selection; other books not represented'}
        return {'source_id': f'scripture:{edition}:JAS', 'value': text, 'mode': 'usfm',
                'kind': 'scripture_book', 'metadata': metadata}
    if (acquisition.get('purpose') != 'scripture_crossrefs_archive'
            or acquisition.get('requested_url') != DATASET_URL):
        raise ValueError('Unknown scripture acquisition purpose')
    selected = select_crossrefs(raw)
    metadata = {**DATASET_RIGHTS, 'archive_member': selected['member'],
                'member_sha256': selected['member_sha256'], 'header': selected['header'],
                'dataset_rows': selected['dataset_rows'], 'selected_rows': len(selected['records']),
                'transformation': SELECTION_VERSION, 'scope': 'James-outbound references only'}
    return {'source_id': 'scripture:openbible:JAS-outbound', 'value': selected['text'],
            'mode': 'text', 'kind': 'scripture_cross_references', 'metadata': metadata}


def import_james_capture(receipt_path: Path, output: Path) -> dict:
    """Import exactly three explicitly selected local downloads into a fresh capture.

    The receipt is an operator-supplied acquisition record, not an attestation of
    remote authenticity. Verify hashes and fixed official URL identities before
    reading or writing the representation. Paths must stay beside the receipt.
    """
    receipt_path, output = Path(receipt_path), Path(output)
    if (not receipt_path.is_absolute() or receipt_path.resolve() != receipt_path
            or receipt_path.stat().st_size > 65536):
        raise ValueError('Require a bounded explicit canonical acquisition receipt')
    if not output.is_absolute() or output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError('Require an absolute output under an existing canonical parent')
    if output.exists() or output.is_symlink():
        raise FileExistsError('The James capture directory must be fresh')
    with receipt_path.open('rb') as stream:
        receipt_raw = stream.read(65537)
    if len(receipt_raw) > 65536:
        raise ValueError('Acquisition receipt exceeds its byte budget')
    receipt = json.loads(receipt_raw)
    rows = receipt.get('sources')
    if receipt.get('schema') != 'source-research-receipt/1' or type(rows) is not list or len(rows) != 3:
        raise ValueError('Require the three-source acquisition receipt')
    expected = {**EDITION_URLS, 'openbible': DATASET_URL}
    seen, checked = set(), []
    for row in rows:
        edition = row.get('edition')
        if (edition not in expected or edition in seen or row.get('url') != expected[edition]
                or row.get('final_url') != expected[edition] or row.get('status') != 200):
            raise ValueError('Acquisition must identify each official source exactly once')
        path = Path(row['path'])
        if (not path.is_absolute() or path.resolve() != path or path.parent != receipt_path.parent
                or not path.is_file() or path.stat().st_size > MAX_ARCHIVE):
            raise ValueError('Archive must be a bounded regular sibling of its receipt')
        raw = path.read_bytes()
        if sha256(raw) != row.get('sha256') or len(raw) != row.get('bytes'):
            raise ValueError('Archive does not match its acquisition receipt')
        acquired_at = row.get('acquired_at')
        if type(acquired_at) is not str or datetime.fromisoformat(acquired_at).tzinfo is None:
            raise ValueError('Acquisition time must include a timezone')
        acquisition = {'id': 'scripture:' + edition, 'requested_url': expected[edition],
                       'final_url': expected[edition], 'status': 200, 'media_type': 'application/zip',
                       'encoding': 'utf-8', 'body_sha256': sha256(raw),
                       'raw_path': f'raw/{sha256(raw)}.bin', 'captured_at': acquired_at,
                       'purpose': 'scripture_crossrefs_archive' if edition == 'openbible' else 'scripture_usfm_archive',
                       'headers': {k.lower(): v for k, v in row.get('headers', {}).items()
                                   if k.lower() in {'etag', 'last-modified', 'content-type'}},
                       'provenance': 'operator-supplied local acquisition receipt; hashes checked'}
        if edition != 'openbible':
            acquisition['edition_id'] = edition
        source = scripture_source(acquisition, raw)
        if source['mode'] == 'usfm':
            _complete_book(parse_usfm_document(source['value']))
        checked.append((acquisition, raw))
        seen.add(edition)
    output.mkdir(mode=0o700)
    for acquisition, raw in checked:
        _write(output / acquisition['raw_path'], raw)
    acquisitions = [item[0] for item in checked]
    inventory = {'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
                 'seed_url': EDITION_URLS['engwebp'], 'scope': 'James, two editions, one cross-reference dataset',
                 'acquisitions': 3, 'rights': [scripture_source(a, raw)['metadata'] for a, raw in checked],
                 'source_receipt_sha256': sha256(receipt_raw),
                 'training_performed': False, 'model_calls': 0}
    coverage = {'schema_version': 1, 'complete_within_declared_scope': True,
                'scope': inventory['scope'], 'other_books_represented': False,
                'original_archive_scope': 'Publisher ZIPs contain other books; only James selected for representation.',
                'omissions': ['Other Bible books and external cross-reference target bodies',
                              'Greek/manuscript analysis, historical verification and settled theology']}
    _write(output / 'acquisitions.jsonl', b''.join(canonical(a) for a in acquisitions))
    _write(output / 'documents.jsonl', b'')
    _write(output / 'inventory.json', canonical(inventory))
    _write(output / 'coverage.json', canonical(coverage))
    return {'output_dir': str(output), 'inventory': inventory, 'coverage': coverage}


def _verse_index(corpus: Corpus, edition: str) -> dict:
    source = corpus.source(f'scripture:{edition}:JAS')
    verses = source['ast']['verses']
    _complete_book(source['ast'])
    index = {}
    for verse in verses:
        identifier = verse['id']
        start, end = verse['text_span']
        if identifier in index or verse['text'] != source['text'][start:end] or not verse['text'].strip():
            raise ValueError('Duplicate, empty or inconsistent verse projection')
        index[identifier] = {'edition_id': edition, 'passage_id': identifier,
                             'source_id': source['source_id'], 'source_sha256': source['source_sha256'],
                             'text_sha256': source['text_sha256'], 'text_span': [start, end]}
    return index


def _resolve(reference: str, index: dict) -> dict:
    endpoints = reference.split('-')
    if not all(value.startswith('Jas.') for value in endpoints):
        return {'status': 'outside_pilot_scope', 'passages': []}
    first = tuple(map(int, endpoints[0].split('.')[1:]))
    last = tuple(map(int, endpoints[-1].split('.')[1:]))
    if first > last:
        return {'status': 'invalid_range', 'passages': []}
    keys = sorted(index, key=lambda value: tuple(map(int, value.split('.')[1:])))
    requested = [key for key in keys if first <= tuple(map(int, key.split('.')[1:])) <= last]
    if (f'JAS.{first[0]}.{first[1]}' not in index or f'JAS.{last[0]}.{last[1]}' not in index):
        return {'status': 'unresolved_in_selected_edition', 'passages': []}
    return {'status': 'resolved_address_only; equivalence_not_established',
            'passages': [index[key] for key in requested]}


def pilot_relationships(corpus: Corpus) -> dict:
    """Resolve only extant James addresses, preserving editorial assertions and gaps."""
    editions = {edition: _verse_index(corpus, edition) for edition in EDITION_URLS}
    source = corpus.source('scripture:openbible:JAS-outbound')
    from graphrag_core.lessons_corpus.corpus import _blob
    acquisition = corpus.acquisitions[source['acquisition_id']]
    selected = select_crossrefs(_blob(corpus.root.parent, acquisition))
    edges, statuses = [], Counter()
    for row in selected['records']:
        start, end = row['text_span']
        if source['text'][start:end] != row['quote']:
            raise ValueError('Cross-reference citation does not match selected original wording')
        mappings = {edition: {'from': _resolve(row['from'], index), 'to': _resolve(row['to'], index)}
                    for edition, index in editions.items()}
        statuses[mappings['engwebp']['to']['status']] += 1
        edges.append({**row, 'kind': 'editorial_cross_reference', 'dataset': DATASET_RIGHTS,
                      'original_member_sha256': selected['member_sha256'], 'editions': mappings,
                      'citation': {'source_id': source['source_id'], 'source_sha256': source['source_sha256'],
                                   'text_sha256': source['text_sha256'], 'start_char': start,
                                   'end_char': end, 'quote': row['quote']}})
    return {'schema': 'james-relationships/1', 'edition_verse_counts': {key: len(value) for key, value in editions.items()},
            'dataset_rows': selected['dataset_rows'], 'selected_rows': len(edges), 'target_status_counts': dict(statuses),
            'relationships': edges, 'attribution': DATASET_RIGHTS,
            'limitations': ['Address matching is not translation equivalence or independent corroboration.',
                            'Other-book targets remain unresolved and are not fetched.']}
