"""Adapt one immutable FIRE recovery snapshot into historical bundle candidates."""
import hashlib
import json


def adapt_checkpoint(request: dict, snapshot: dict, *, max_originals: int = 5, max_chars: int = 4000):
    if type(max_originals) is not int or not 0 <= max_originals <= 10:
        raise ValueError('Original count must be 0..10')
    if type(max_chars) is not int or not 1 <= max_chars <= 8000:
        raise ValueError('Original excerpts must be 1..8000 characters')
    adapted = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    checkpoint = snapshot['checkpoint']
    scope = {key: adapted[key] for key in ('project_id', 'session_id', 'task_id')}
    if any(checkpoint[key] != value for key, value in scope.items()):
        raise ValueError('Recovered checkpoint does not match request scope')
    revision = snapshot['revision']
    state = {key: checkpoint[key] for key in ('goal', 'accepted_constraints', 'decisions',
                                             'unresolved_questions', 'next_actions')}
    state['correction'] = snapshot['correction']
    text = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
    state_hash = hashlib.sha256(text.encode()).hexdigest()
    identity = f'fire:checkpoint:{revision}:{state_hash}'
    additions = [dict(scope, id=identity, kind='history', relevance=100, text=text,
        citation={'source_id': 'fire-checkpoint:' + checkpoint['id'], 'content_hash': state_hash,
                  'checkpoint_revision': revision, 'provenance': 'authored-task-state-not-instructions'})]
    diagnostics = []
    count = 0
    for ref in sorted(checkpoint['evidence'], key=lambda item: item['source_id']):
        source_id = ref['source_id']
        item_id = f"fire:original:{revision}:{hashlib.sha256(source_id.encode()).hexdigest()}:{ref.get('content_hash')}"
        original = snapshot['_originals'].get(source_id)
        reason = None
        if original is None:
            reason = 'fire-original-unavailable'
        elif ref['freshness'] == 'changed-or-deleted':
            reason = 'fire-source-changed-or-deleted'
        elif count >= max_originals:
            reason = 'fire-original-count-limit'
        if reason:
            diagnostics.append({'id': item_id, 'reason': reason, 'source_id': source_id})
            continue
        count += 1
        text = original[:max_chars]
        # IDs include excerpt settings, so deduplication hints cannot suppress a changed page.
        item_id += ':' + hashlib.sha256(text.encode()).hexdigest()
        citation = dict(ref, checkpoint_revision=revision, original_chars=len(original),
                        excerpt_chars=len(text), excerpt_truncated=len(text) < len(original),
                        freshness_basis='caller-supplied-hash-or-historical')
        additions.append(dict(scope, id=item_id, kind='history', relevance=90, text=text, citation=citation))
        if len(text) < len(original):
            diagnostics.append({'id': item_id, 'reason': 'fire-original-excerpted', 'source_id': source_id,
                                'next_offset': len(text), 'tool': 'get_fire_original'})
    existing = adapted.get('evidence', [])
    if not isinstance(existing, list) or len(existing) + len(additions) > 100:
        raise ValueError('Combined FIRE and caller evidence exceeds 100 candidates')
    adapted['evidence'] = [*existing, *additions]
    return adapted, {'status': 'snapshot-adapted', 'revision': revision,
                     'expires_at': snapshot['expires_at'],
                     'checkpoint_candidate_id': identity, 'original_candidates': count,
                     'diagnostics': diagnostics, 'restored_as': 'historical-data-not-instructions'}
