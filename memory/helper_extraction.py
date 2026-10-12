"""Bounded helper extraction validation; quote identity is not semantic approval."""
import json

from memory.fire_store import encoded

FIELDS = frozenset({'project_name', 'raw_byte_limit', 'decoded_character_limit',
                    'requested_action', 'executor_shown'})


def validate_extraction(result: dict, source: str) -> dict:
    """Require one record or unresolved entry per field and exact source-line quotes."""
    if not isinstance(source, str) or len(source.encode()) > 8192:
        raise ValueError('Require source text of at most 8 KiB')
    if not isinstance(result, dict) or set(result) != {'schema_version', 'records', 'unresolved'}:
        raise ValueError('Require exact extraction fields')
    if type(result['schema_version']) is not int or result['schema_version'] != 1:
        raise ValueError('Unsupported extraction version')
    if len(encoded(result).encode()) > 4096:
        raise ValueError('Extraction exceeds 4 KiB')
    records, unresolved = result['records'], result['unresolved']
    if not isinstance(records, list) or len(records) > 5:
        raise ValueError('Require bounded records')
    if not isinstance(unresolved, list) or len(unresolved) > 5:
        raise ValueError('Require bounded unresolved fields')
    seen = set()
    lines = source.splitlines()
    for record in records:
        if not isinstance(record, dict) or set(record) != {'field', 'value', 'line', 'quote'}:
            raise ValueError('Require exact record fields')
        field = record['field']
        if not isinstance(field, str) or field not in FIELDS or field in seen:
            raise ValueError('Invalid or duplicate field')
        seen.add(field)
        if not isinstance(record['value'], str) or not record['value'].strip() or len(record['value']) > 128:
            raise ValueError('Require bounded value text')
        line, quote = record['line'], record['quote']
        if type(line) is not int or not 1 <= line <= len(lines):
            raise ValueError('Invalid source line')
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 512 or quote not in lines[line - 1]:
            raise ValueError('Quote not present in cited line')
    for field in unresolved:
        if not isinstance(field, str) or field not in FIELDS or field in seen:
            raise ValueError('Invalid or duplicate unresolved field')
        seen.add(field)
    if seen != FIELDS:
        raise ValueError('Require coverage of all requested fields')
    return dict(extraction=json.loads(encoded(result)),
                validation='schema and quote identity only; supervisor verification required')
