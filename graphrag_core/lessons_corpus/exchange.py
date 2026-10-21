"""Explicit, data-only research review packets and local lesson releases.

These artifacts grant no tools, inject no prompts and import no operational state.
Human identities and rights bases are operator attestations, not authentication.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import re
import stat

from graphrag_core.lessons_corpus.acquire import _write_private
from graphrag_core.lessons_corpus.corpus import Corpus, _safe_file, canonical, sha256
from graphrag_core.lessons_corpus.ledger import (
    KINDS, MAX_INVESTIGATION_REFS, _CITATION_FIELDS, _EVENT_FIELDS, _MAX_RECORD_BYTES, _RECORD_FIELDS,
    _encoded, _hash, _identifier, _identity, _investigation_refs, _object, _revision, _text,
)
from graphrag_core.lessons_corpus.ledger_reader import LessonLedgerReader


PACKET_SCHEMA = 'free-man-review-packet/2'
BUNDLE_SCHEMA = 'free-man-lesson-exchange/2'
_LEGACY_PACKET_SCHEMA = 'free-man-review-packet/1'
_LEGACY_BUNDLE_SCHEMA = 'free-man-lesson-exchange/1'
MAX_BYTES = 1024 * 1024
MAX_LESSONS = 10
DESTINATION = 'rest-proxy:evidence-only'
USE = 'local_inference_evidence_only'
VERIFICATION = 'citation_integrity_only; identities_operator_supplied; attributed_research_data'
def _constraints() -> dict:
    return {'data_only': True, 'tool_permissions': [], 'automatic_prompt_injection': False,
            'training_allowed': False, 'redistribution_allowed': False,
            'source_bodies_included': False, 'authenticated_reviewer_identity': False}


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('Require an exact SHA-256 fingerprint')
    return value


def _bounded(value: dict) -> bytes:
    # canonical() preserves the corpus convention; reject nonfinite authored JSON.
    _encoded(value)
    raw = canonical(value)
    if len(raw) > MAX_BYTES:
        raise ValueError('Research exchange artifact exceeds its 1 MiB budget')
    return raw


def read_artifact(path: Path) -> dict:
    """Read bounded explicit JSON without treating contents as paths or commands."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not path.is_file():
        raise ValueError('Require an explicit canonical artifact file')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Research exchange artifact exceeds its byte budget')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate artifact JSON field')
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('Artifact JSON must be finite')

    try:
        result = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid exchange artifact JSON') from error
    if type(result) is not dict:
        raise ValueError('Artifact must be a JSON object')
    return result


def write_artifact(path: Path, value: dict) -> str:
    """Create a fresh owner-only artifact under an existing private directory."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not path.parent.is_dir():
        raise ValueError('Require a fresh canonical output in an existing private directory')
    info = path.parent.stat()
    if (stat.S_IMODE(info.st_mode) != 0o700
            or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
        raise ValueError('Output directory requires current-user ownership and mode 700')
    raw = _bounded(value)
    _write_private(path, raw)
    return sha256(raw)


def _generation(corpus: Corpus) -> str:
    if not corpus.root.is_absolute() or corpus.root.resolve() != corpus.root:
        raise ValueError('Require an explicit canonical corpus root')
    raw = _safe_file(corpus.root, 'manifest.json').read_bytes()
    if json.loads(raw) != corpus.manifest:
        raise ValueError('Selected source generation changed')
    corpus._checked('sources.jsonl')
    for name, expected in corpus.manifest['capture_metadata_hashes'].items():
        path = _safe_file(corpus.root.parent, name)
        if sha256(path.read_bytes()) != expected:
            raise ValueError('Selected capture metadata changed')
    return sha256(raw)


def make_review_packet(corpus: Corpus, ledger_path: Path, selections: list[dict]) -> dict:
    """Bind current validated lesson revisions to the explicitly selected corpus."""
    if type(selections) is not list or not 1 <= len(selections) <= MAX_LESSONS:
        raise ValueError('Select 1 through 10 exact lesson revisions')
    manifest_hash = _generation(corpus)
    reader = LessonLedgerReader(ledger_path, corpus.source)
    seen = set()
    for selection in selections:
        if type(selection) is not dict or set(selection) != {'lesson_id', 'revision', 'revision_sha256'}:
            raise ValueError('Selection requires lesson_id, revision and revision_sha256')
        identifier = selection['lesson_id']
        if type(identifier) is not str or identifier in seen:
            raise ValueError('Lesson selections must be distinct identifiers')
        seen.add(identifier)
        _digest(selection['revision_sha256'])
    selected = reader.read_investigated(selections)
    if _generation(corpus) != manifest_hash:
        raise ValueError('Source generation changed during packet preparation')
    packet = {'schema': PACKET_SCHEMA, 'corpus_manifest_sha256': manifest_hash,
              **selected, 'verification': VERIFICATION}
    _packet_events(packet)
    return packet


def _checked_event(event: object) -> dict:
    if type(event) is not dict or set(event) != _EVENT_FIELDS | {'revision_sha256'}:
        raise ValueError('Invalid selected revision envelope')
    payload = {key: value for key, value in event.items() if key != 'revision_sha256'}
    if _hash(_encoded(payload)) != _digest(event['revision_sha256']):
        raise ValueError('Selected revision fingerprint mismatch')
    _event_structure(payload)
    return event


def _packet_events(packet: dict, *, allow_legacy: bool = False) -> list[dict]:
    legacy = type(packet) is dict and packet.get('schema') == _LEGACY_PACKET_SCHEMA
    fields = {'schema', 'corpus_manifest_sha256', 'lessons', 'verification'}
    if not legacy:
        fields.add('investigations')
    if (type(packet) is not dict or set(packet) != fields
            or (legacy and not allow_legacy)
            or type(packet['schema']) is not str
            or packet['schema'] not in {PACKET_SCHEMA, _LEGACY_PACKET_SCHEMA}
            or packet['verification'] != VERIFICATION
            or type(packet['lessons']) is not list or not 1 <= len(packet['lessons']) <= MAX_LESSONS):
        raise ValueError('Unsupported review packet contract')
    _digest(packet['corpus_manifest_sha256'])
    seen = set()
    for event in packet['lessons']:
        _checked_event(event)
        identifier = event['lesson_id']
        if identifier in seen:
            raise ValueError('Duplicate or invalid selected lesson')
        if legacy:
            if event['schema_version'] != 1:
                raise ValueError('Legacy packet requires legacy event structures')
        elif event['schema_version'] != 2 or event['record']['kind'] != 'proposed_lesson':
            raise ValueError('Review packet requires investigated version-2 proposed lessons')
        seen.add(identifier)
    if not legacy:
        _packet_investigations(packet, seen)
    _bounded(packet)
    return packet['lessons']


def _packet_investigations(packet: dict, lesson_ids: set[str]) -> None:
    """Bind exact reviewed findings without promoting review decisions to truth."""
    findings = packet['investigations']
    if type(findings) is not list or not 1 <= len(findings) <= MAX_LESSONS * MAX_INVESTIGATION_REFS:
        raise ValueError('Require bounded reviewed investigation revisions')
    included = {}
    for event in findings:
        _checked_event(event)
        identifier = event['lesson_id']
        if identifier in included or identifier in lesson_ids:
            raise ValueError('Duplicate investigation or selected lesson used as its own finding')
        if (event['record']['kind'] == 'proposed_lesson' or event['operation'] != 'review'
                or event['status'] not in {'accepted', 'disputed', 'rejected'}):
            raise ValueError('Investigation requires a reviewed non-lesson revision')
        included[identifier] = event
    if list(included) != sorted(included):
        raise ValueError('Investigation revisions must use stable identifier order')
    referenced = set()
    for lesson in packet['lessons']:
        for reference in _investigation_refs(lesson['record']['investigation_refs']):
            identifier = reference['record_id']
            event = included.get(identifier)
            if (event is None or event['revision'] != reference['revision']
                    or event['revision_sha256'] != reference['revision_sha256']):
                raise ValueError('Investigation reference does not bind an exact included reviewed revision')
            referenced.add(identifier)
    if referenced != set(included):
        raise ValueError('Packet includes unreferenced investigation revisions')


def _citations_structure(citations: object) -> None:
    if type(citations) is not list or len(citations) > 128:
        raise ValueError('Require bounded citation lists')
    for citation in citations:
        _object(citation, _CITATION_FIELDS, 'Citation')
        _text(citation['source_id'], 'Citation source', 2048)
        _digest(citation['source_sha256'])
        _digest(citation['text_sha256'])
        start, end = citation['start_char'], citation['end_char']
        wording = _text(citation['quote'], 'Citation quote')
        if (type(start) is not int or type(end) is not int or not 0 <= start < end
                or end - start != len(wording)):
            raise ValueError('Citation span does not match its quoted character count')


def _event_structure(event: dict) -> None:
    """Check versioned syntax without claiming source or history authentication."""
    if type(event['schema_version']) is not int or event['schema_version'] not in {1, 2}:
        raise ValueError('Unsupported lesson event schema')
    _identifier(event['lesson_id'])
    _revision(event['revision'])
    _digest(event['previous_hash'])
    if event['verification'] != 'citation_integrity_only':
        raise ValueError('Unsupported lesson verification assertion')
    try:
        when = datetime.fromisoformat(_text(event['created_at'], 'Revision timestamp', 256))
        if when.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('Invalid lesson revision timestamp') from None
    record = event['record']
    fields = _RECORD_FIELDS | {'outcomes'} if type(record) is dict and 'outcomes' in record else _RECORD_FIELDS
    if event['schema_version'] == 2 and type(record) is dict and record.get('kind') == 'proposed_lesson':
        fields |= {'investigation_refs'}
    _object(record, fields, 'Lesson record')
    if type(record['kind']) is not str or record['kind'] not in KINDS:
        raise ValueError('Unsupported lesson record kind')
    _identity(record['author'])
    for key in ('statement', 'context', 'uncertainty'):
        _text(record[key], key)
    for key in ('values', 'affected_parties'):
        if type(record[key]) is not list or len(record[key]) > 128:
            raise ValueError('Require bounded lesson labels')
        for value in record[key]:
            _text(value, key, 2048)
    for key in ('citations', 'counter_citations'):
        _citations_structure(record[key])
    if 'investigation_refs' in record:
        _investigation_refs(record['investigation_refs'])
    if 'outcomes' in record:
        if type(record['outcomes']) is not list or len(record['outcomes']) > 128:
            raise ValueError('Require bounded outcomes')
        for outcome in record['outcomes']:
            _object(outcome, frozenset({'kind', 'description', 'method', 'citations'}), 'Outcome')
            if type(outcome['kind']) is not str or outcome['kind'] not in {'reported', 'measured', 'predicted'}:
                raise ValueError('Unsupported outcome classification')
            _text(outcome['description'], 'Outcome description')
            _text(outcome['method'], 'Outcome method')
            _citations_structure(outcome['citations'])
            if not outcome['citations'] and outcome['kind'] != 'predicted':
                raise ValueError('Non-predicted outcomes require cited evidence')
    if len(_encoded(record).encode('utf-8')) > _MAX_RECORD_BYTES:
        raise ValueError('Lesson record exceeds its byte budget')
    if event['operation'] == 'save':
        if event['status'] != 'proposed' or event['review'] is not None:
            raise ValueError('Invalid proposed lesson event')
    elif event['operation'] == 'review':
        review = _object(event['review'], frozenset({'decision', 'reviewer', 'reason', 'provenance'}), 'Review')
        if (type(event['status']) is not str or event['status'] not in {'accepted', 'disputed', 'rejected'}
                or review['decision'] != event['status'] or review['provenance'] != 'operator_supplied'):
            raise ValueError('Invalid review decision or provenance')
        reviewer = review['reviewer']
        if type(reviewer) is dict and reviewer.get('kind') == 'operator':
            _object(reviewer, frozenset({'kind', 'name'}), 'Operator reviewer')
            _text(reviewer['name'], 'Reviewer', 512)
        else:
            _identity(reviewer, reviewer=True)
        _text(review['reason'], 'Review reason')
    else:
        raise ValueError('Unsupported lesson revision operation')


def _accepted_by_human(event: dict) -> None:
    review = event['review']
    if (event['status'] != 'accepted' or event['operation'] != 'review' or type(review) is not dict
            or review.get('decision') != 'accepted' or type(review.get('reviewer')) is not dict
            or review['reviewer'].get('kind') != 'human'):
        raise ValueError('Release requires the current exact revision to be accepted by an explicit human reviewer')
    _identity(review['reviewer'], reviewer=True)


def _release_record(release: dict, packet: dict) -> dict:
    fields = {'expected_packet_sha256', 'destination', 'reviewer', 'reason', 'rights_basis', 'use'}
    if type(release) is not dict or set(release) != fields:
        raise ValueError('Release requires an exact packet, destination, human reviewer, reason, rights and use')
    if _digest(release['expected_packet_sha256']) != sha256(_bounded(packet)):
        raise ValueError('Release acknowledgment does not match this exact review packet')
    if release['destination'] != DESTINATION or release['use'] != USE:
        raise ValueError('Only explicit local Rest-proxy evidence use is supported')
    reviewer = _identity(release['reviewer'], reviewer=True)
    if reviewer['kind'] != 'human':
        raise ValueError('Release requires an explicit human operator attestation')
    return {**release, 'reviewer': reviewer, 'reason': _text(release['reason'], 'Release reason', 4096),
            'rights_basis': _text(release['rights_basis'], 'Local evidence rights basis', 4096)}


def release_bundle(corpus: Corpus, ledger_path: Path, packet: dict, release: dict) -> dict:
    """Prepare a private data bundle; neither copy into nor mutate Rest-proxy state."""
    lessons = _packet_events(packet)
    selections = [{key: event[key] for key in ('lesson_id', 'revision', 'revision_sha256')} for event in lessons]
    current = make_review_packet(corpus, ledger_path, selections)
    if current != packet:
        raise ValueError('Review packet is stale or does not match validated current source evidence')
    for event in lessons:
        _accepted_by_human(event)
    release = _release_record(release, packet)
    payload = {'packet': packet, 'release': release, 'constraints': _constraints()}
    bundle = {'schema': BUNDLE_SCHEMA, 'payload': payload, 'payload_sha256': sha256(_bounded(payload))}
    _bounded(bundle)
    return bundle


def inspect_bundle(bundle: dict, expected_sha256: str) -> dict:
    """Inspect a fingerprint-pinned historical artifact without installing lessons.

    Exact currentness still requires comparing to the source ledger/corpus. Hashes
    and supplied human names do not establish authenticity, identity or truth.
    """
    if type(bundle) is not dict:
        raise ValueError('Unsupported lesson exchange bundle')
    if sha256(_bounded(bundle)) != _digest(expected_sha256):
        raise ValueError('Bundle does not match the explicitly selected fingerprint')
    if (set(bundle) != {'schema', 'payload', 'payload_sha256'}
            or type(bundle['schema']) is not str
            or bundle['schema'] not in {BUNDLE_SCHEMA, _LEGACY_BUNDLE_SCHEMA}
            or type(bundle['payload']) is not dict
            or set(bundle['payload']) != {'packet', 'release', 'constraints'}):
        raise ValueError('Unsupported lesson exchange bundle')
    payload = bundle['payload']
    if (sha256(_bounded(payload)) != _digest(bundle['payload_sha256'])
            or payload['constraints'] != _constraints()):
        raise ValueError('Bundle payload or fixed use constraints differ')
    legacy = bundle['schema'] == _LEGACY_BUNDLE_SCHEMA
    expected_packet = _LEGACY_PACKET_SCHEMA if legacy else PACKET_SCHEMA
    if type(payload['packet']) is not dict or payload['packet'].get('schema') != expected_packet:
        raise ValueError('Bundle and packet schema versions do not match')
    lessons = _packet_events(payload['packet'], allow_legacy=legacy)
    for event in lessons:
        _accepted_by_human(event)
    _release_record(payload['release'], payload['packet'])
    return {'schema': bundle['schema'], 'destination': DESTINATION,
            'bundle_sha256': expected_sha256,
            'lessons': [{key: event[key] for key in ('lesson_id', 'revision', 'revision_sha256', 'status')}
                        for event in lessons], 'constraints': _constraints(),
            'currentness': 'not_checked_against_source_ledger',
            'investigation_prerequisite': ('unavailable_in_legacy_artifact' if legacy
                                          else 'bound_reviewed_revisions; currentness_not_checked'),
            'investigations': [{key: event[key] for key in (
                'lesson_id', 'revision', 'revision_sha256', 'status')}
                for event in payload['packet'].get('investigations', [])],
            'verification': 'artifact_consistency_only; no import, execution or reviewer_authentication'}
