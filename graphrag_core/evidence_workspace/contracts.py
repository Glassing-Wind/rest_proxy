"""Bounded source packages and citations; quoted evidence is not an instruction."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
import hashlib
import io
import json
import re

from memory.fire_store import encoded

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
MAX_TOTAL_SOURCE_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 512 * 1024
MAX_BACKUP_BYTES = 56 * 1024 * 1024
MAX_ARCHIVE_JSON_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 48 * 1024 * 1024
MAX_EVENTS = 1024
MAX_SOURCES = 64
MAX_RELATIONSHIPS = 256
MAX_ENTITIES = 128
MAX_CASES = 32
MAX_REVISION = (1 << 63) - 2
TEXT_MEDIA = frozenset({'text/plain', 'text/csv', 'application/json'})
IMAGE_MEDIA = {'image/png': 'PNG', 'image/jpeg': 'JPEG'}
EVIDENCE_STATUS = frozenset({'observation', 'source_claim', 'inference'})
REVIEW_DECISIONS = frozenset({'accepted', 'rejected', 'amended'})
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


class WorkspaceError(ValueError):
    """Invalid bounded input, unavailable source, or private-state boundary."""


class WorkspaceConflict(WorkspaceError):
    """A case already exists or its expected revision changed."""


class WorkspaceIntegrityError(WorkspaceError):
    """Stored or restored evidence failed consistency validation."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identifier(value) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise WorkspaceError('Identifiers require 1..64 ASCII letters, numbers, hyphens or underscores')
    return value


def text(value, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or '\0' in value or (required and not value.strip()):
        raise WorkspaceError('Invalid or oversized text field')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError as error:
        raise WorkspaceError('Text fields require valid Unicode') from error
    if size > maximum:
        raise WorkspaceError('Invalid or oversized text field')
    return value


def object_fields(value, required: set, optional: set = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise WorkspaceError('Unexpected or missing object fields')
    return value


def timestamp(value: str) -> str:
    text(value, 80)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise WorkspaceError('Source timestamps require an ISO date and explicit timezone') from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkspaceError('Source timestamps require an explicit timezone')
    return value


def sha256(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise WorkspaceError('Hashes require lowercase SHA256')
    return value


def finite_json(value, limit: int) -> str:
    try:
        raw = encoded(value)
        size = len(raw.encode())
    except (ValueError, TypeError, RecursionError, UnicodeEncodeError) as error:
        raise WorkspaceError('Require finite bounded JSON') from error
    if size > limit:
        raise WorkspaceError('JSON exceeds its byte budget')
    return raw


def read_json(raw: bytes):
    """Reject ambiguous fields, nonfinite numbers and excessive parser nesting."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WorkspaceError('Duplicate JSON fields are unsupported')
            result[key] = value
        return result

    def nonfinite(value):
        raise WorkspaceError('Require finite JSON')

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise WorkspaceError('Invalid bounded JSON') from error


def decode_content(value) -> bytes:
    if not isinstance(value, str) or len(value) > ((MAX_SOURCE_BYTES + 2) // 3) * 4:
        raise WorkspaceError('Encoded source exceeds its byte budget')
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise WorkspaceError('Source content must use valid base64') from error
    if len(content) > MAX_SOURCE_BYTES:
        raise WorkspaceError('Source exceeds its byte budget')
    return content


def content_details(content: bytes, media_type: str) -> dict:
    if len(content) > MAX_SOURCE_BYTES:
        raise WorkspaceError('Source exceeds its byte budget')
    if media_type in TEXT_MEDIA:
        if len(content) > MAX_TEXT_BYTES:
            raise WorkspaceError('Text source exceeds 256 KiB')
        try:
            original = content.decode('utf-8')
        except UnicodeDecodeError as error:
            raise WorkspaceError('Text sources must be UTF-8') from error
        if '\0' in original:
            raise WorkspaceError('Text source contains a null character')
        lines = original.splitlines()
        if len(lines) > 100000 or any(len(line.encode()) > 16384 for line in lines):
            raise WorkspaceError('Source line count or line byte budget exceeded')
        return {'line_count': len(lines)}
    if media_type not in IMAGE_MEDIA:
        raise WorkspaceError('Unsupported media type')
    try:
        from PIL import Image
    except ImportError as error:
        raise WorkspaceError('Image imports require the optional Pillow dependency') from error
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if (image.format != IMAGE_MEDIA[media_type] or width * height > 16000000
                    or getattr(image, 'n_frames', 1) != 1):
                raise WorkspaceError('Image format, pixel budget or animation is unsupported')
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
        return {'dimensions': [width, height]}
    except WorkspaceError:
        raise
    except Exception as error:
        raise WorkspaceError('Image content could not be validated') from error


def validate_source(value: dict) -> tuple[dict, bytes | None]:
    object_fields(value, {'id', 'label', 'kind', 'media_type'},
                  {'text', 'content_base64', 'availability', 'sha256', 'observed_at',
                   'original_reference', 'source_revision'})
    source = {'id': identifier(value['id']), 'label': text(value['label'], 240),
              'kind': text(value['kind'], 64), 'media_type': value['media_type']}
    if not isinstance(value['media_type'], str) or value['media_type'] not in TEXT_MEDIA | IMAGE_MEDIA.keys():
        raise WorkspaceError('Unsupported source media type')
    if 'text' in value and 'content_base64' in value:
        raise WorkspaceError('Supply text or base64, not both')
    content = None
    if 'text' in value:
        if value['media_type'] not in TEXT_MEDIA:
            raise WorkspaceError('Image sources require base64 original bytes')
        content = text(value['text'], MAX_TEXT_BYTES, required=False).encode()
    elif 'content_base64' in value:
        content = decode_content(value['content_base64'])
    expected = sha256(value['sha256']) if value.get('sha256') is not None else None
    availability = 'stored' if content is not None else 'unavailable'
    if value.get('availability', availability) != availability:
        raise WorkspaceError('Source availability does not match supplied content')
    if content is not None:
        actual = digest(content)
        if expected is not None and expected != actual:
            raise WorkspaceError('Supplied source hash does not match bytes')
        source.update(content_details(content, value['media_type']))
        source.update(sha256=actual, bytes=len(content))
    else:
        source.update(sha256=expected, bytes=0)
    source['availability'] = availability
    source['observed_at'] = timestamp(value['observed_at']) if value.get('observed_at') is not None else None
    source['original_reference'] = text(value.get('original_reference', ''), 1024, required=False)
    source['source_revision'] = text(value.get('source_revision', ''), 128, required=False)
    return source, content


def validate_citation(value: dict, sources: dict, contents: dict) -> dict:
    object_fields(value, {'source_id'}, {'start_line', 'end_line', 'quote', 'bbox'})
    source_id = identifier(value['source_id'])
    if source_id not in sources or sources[source_id]['availability'] != 'stored':
        raise WorkspaceError('Citations require a stored source in this case')
    source = sources[source_id]
    citation = {'source_id': source_id}
    quote = text(value['quote'], 4000) if 'quote' in value else None
    if source['media_type'] in TEXT_MEDIA:
        if 'bbox' in value or 'start_line' not in value or 'end_line' not in value:
            raise WorkspaceError('Text citations require inclusive line bounds')
        first, last = value['start_line'], value['end_line']
        lines = contents[source_id].decode().splitlines()
        if (type(first) is not int or type(last) is not int
                or not 1 <= first <= last <= len(lines) or last - first >= 100):
            raise WorkspaceError('Citation lines are outside the source or excerpt budget')
        if quote is not None and quote not in '\n'.join(lines[first - 1:last]):
            raise WorkspaceError('Citation quote is not present within its source lines')
        citation.update(start_line=first, end_line=last, validation='source-lines-checked')
    else:
        if 'start_line' in value or 'end_line' in value or 'bbox' not in value:
            raise WorkspaceError('Image citations require a normalized region')
        box = object_fields(value['bbox'], {'x', 'y', 'width', 'height'})
        if any(type(number) not in (int, float) for number in box.values()):
            raise WorkspaceError('Image regions require finite numeric coordinates')
        finite_json(box, 1024)
        x, y, width, height = (box[key] for key in ('x', 'y', 'width', 'height'))
        if not (0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0
                and x + width <= 1 and y + height <= 1):
            raise WorkspaceError('Image region is outside its normalized bounds')
        citation.update(bbox=dict(box), validation='image-region-only')
        if quote is not None:
            citation['transcription_status'] = 'unverified-image-transcription'
    if quote is not None:
        citation['quote'] = quote
    return citation


def validate_package(package: dict) -> tuple[dict, dict]:
    """Detach an explicit package, preserving originals and validating source pointers."""
    object_fields(package, {'schema_version', 'case_id', 'title', 'sources', 'entities', 'relationships'},
                  {'coverage'})
    if type(package['schema_version']) is not int or package['schema_version'] != 1:
        raise WorkspaceError('Unsupported package version')
    baseline = {'schema_version': 1, 'case_id': identifier(package['case_id']),
                'title': text(package['title'], 240)}
    coverage = package.get('coverage', {'status': 'partial', 'notes': ''})
    object_fields(coverage, {'status', 'notes'}, {'expected_source_count'})
    if not isinstance(coverage['status'], str) or coverage['status'] not in {'partial', 'declared_complete'}:
        raise WorkspaceError('Unsupported coverage declaration')
    baseline['coverage'] = {'status': coverage['status'], 'notes': text(coverage['notes'], 2000, required=False)}
    if 'expected_source_count' in coverage:
        count = coverage['expected_source_count']
        if type(count) is not int or not 0 <= count <= 100000:
            raise WorkspaceError('Invalid expected source count')
        baseline['coverage']['expected_source_count'] = count
    raw_sources = package['sources']
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= MAX_SOURCES:
        raise WorkspaceError('Package requires 1..64 sources')
    sources, contents = {}, {}
    for value in raw_sources:
        source, content = validate_source(value)
        if source['id'] in sources:
            raise WorkspaceError('Duplicate source identifier')
        sources[source['id']] = source
        if content is not None:
            contents[source['id']] = content
    if sum(map(len, contents.values())) > MAX_TOTAL_SOURCE_BYTES:
        raise WorkspaceError('Case originals exceed 32 MiB')
    baseline['sources'] = list(sources.values())
    raw_entities = package['entities']
    if not isinstance(raw_entities, list) or not 1 <= len(raw_entities) <= MAX_ENTITIES:
        raise WorkspaceError('Package requires 1..128 entities')
    entities = {}
    for value in raw_entities:
        object_fields(value, {'id', 'label', 'kind'})
        entity = {'id': identifier(value['id']), 'label': text(value['label'], 240),
                  'kind': text(value['kind'], 64)}
        if entity['id'] in entities:
            raise WorkspaceError('Duplicate entity identifier')
        entities[entity['id']] = entity
    baseline['entities'] = list(entities.values())
    raw_relationships = package['relationships']
    if not isinstance(raw_relationships, list) or len(raw_relationships) > MAX_RELATIONSHIPS:
        raise WorkspaceError('Package permits at most 256 relationships')
    relationships = {}
    for value in raw_relationships:
        object_fields(value, {'id', 'source', 'target', 'predicate', 'statement', 'evidence_status', 'citations'},
                      {'explanation'})
        rel = {'id': identifier(value['id']), 'source': identifier(value['source']),
               'target': identifier(value['target']), 'predicate': text(value['predicate'], 128),
               'statement': text(value['statement'], 4000),
               'evidence_status': value['evidence_status'],
               'explanation': text(value.get('explanation', ''), 4000, required=False)}
        if rel['source'] not in entities or rel['target'] not in entities:
            raise WorkspaceError('Relationship endpoints are outside the case')
        if not isinstance(rel['evidence_status'], str) or rel['evidence_status'] not in EVIDENCE_STATUS:
            raise WorkspaceError('Unsupported evidence status')
        if rel['id'] in relationships:
            raise WorkspaceError('Duplicate relationship identifier')
        citations = value['citations']
        if not isinstance(citations, list) or not 1 <= len(citations) <= 16:
            raise WorkspaceError('Relationship requires 1..16 citations')
        rel['citations'] = [validate_citation(cite, sources, contents) for cite in citations]
        relationships[rel['id']] = rel
    baseline['relationships'] = list(relationships.values())
    finite_json(baseline, MAX_METADATA_BYTES)
    return baseline, contents


def validate_event(event: dict, baseline: dict, revision: int) -> dict:
    """Validate imported history without upgrading a claim into a fact."""
    common = {'kind', 'revision', 'created_at'}
    if (not isinstance(event, dict) or type(event.get('revision')) is not int
            or event['revision'] != revision):
        raise WorkspaceIntegrityError('Review history has invalid revision order')
    timestamp(event.get('created_at'))
    if event.get('kind') == 'review':
        object_fields(event, common | {'relationship_id', 'decision', 'reviewer', 'reason'}, {'statement'})
        if identifier(event['relationship_id']) not in {rel['id'] for rel in baseline['relationships']}:
            raise WorkspaceIntegrityError('Review history references an unknown relationship')
        if not isinstance(event['decision'], str) or event['decision'] not in REVIEW_DECISIONS:
            raise WorkspaceIntegrityError('Unsupported review decision')
        text(event['reviewer'], 128)
        text(event['reason'], 4000)
        if event['decision'] == 'amended':
            text(event.get('statement'), 4000)
        elif 'statement' in event:
            raise WorkspaceIntegrityError('Only an amendment may supply replacement text')
    elif event.get('kind') == 'source_check':
        object_fields(event, common | {'source_id', 'current_sha256', 'freshness'})
        source = next((s for s in baseline['sources'] if s['id'] == event['source_id']), None)
        if source is None or source['availability'] != 'stored':
            raise WorkspaceIntegrityError('Check history references an unavailable source')
        current = event['current_sha256']
        if current is not None:
            sha256(current)
        status = 'missing' if current is None else 'matches-supplied-bytes' if current == source['sha256'] else 'changed'
        if status != event['freshness']:
            raise WorkspaceIntegrityError('Check status is inconsistent with source hashes')
    else:
        raise WorkspaceIntegrityError('Unsupported history event')
    finite_json(event, 12000)
    return dict(event)
