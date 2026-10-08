"""Bounded internal capture contract; not a CloudEvents compliance claim."""
import hashlib
import json

EVENT_TYPES = frozenset({'request.recorded', 'response.completed', 'capture.incomplete'})
FIELDS = frozenset({'schema_version', 'event_id', 'scope', 'request_id', 'type',
                    'captured_at', 'payload', 'payload_sha256'})


def canonical(value: object) -> str:
    """Deterministic strict JSON for identity and size checks."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def validate_event(event: dict) -> dict:
    """Return a detached validated event; scope is metadata, not authentication."""
    if not isinstance(event, dict) or set(event) != FIELDS:
        raise ValueError('Require exact capture event fields')
    if type(event['schema_version']) is not int or event['schema_version'] != 1:
        raise ValueError('Unsupported event version')
    for field in ('event_id', 'scope', 'request_id'):
        value = event[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError('Require bounded event identifiers')
    if not isinstance(event['type'], str) or event['type'] not in EVENT_TYPES:
        raise ValueError('Unsupported event type')
    if type(event['captured_at']) is not int or event['captured_at'] < 0:
        raise ValueError('Require nonnegative integer capture timestamp')
    if not isinstance(event['payload'], dict):
        raise ValueError('Require object payload')
    payload = canonical(event['payload']).encode('utf-8')
    if len(payload) > 8192:
        raise ValueError('Payload exceeds 8 KiB')
    if event['payload_sha256'] != hashlib.sha256(payload).hexdigest():
        raise ValueError('Payload identity mismatch')
    raw = canonical(event)
    if len(raw.encode('utf-8')) > 10240:
        raise ValueError('Event exceeds 10 KiB')
    return json.loads(raw)
