"""Explicit, bounded reference investigation into a new private source snapshot.

A fetched counterpart is candidate evidence. Retrieval never rewrites the parent
corpus or establishes equivalence to a missing historical page.
"""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from graphrag_core.lessons_corpus.acquire import _Capture, _URLPolicy, _utc_now, _write_private
from graphrag_core.lessons_corpus.corpus import Corpus, canonical, sha256
from graphrag_core.lessons_corpus.frontier import collect_targets


SCHEMA = 'lessons-reference-recovery/1'
MAX_SELECTIONS = 20
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class _AllowedPolicies:
    """Reuse seed-origin validation for each explicitly permitted public host."""

    def __init__(self, permissions: dict[str, str]):
        self.policies = {}
        for host in permissions:
            policy = _URLPolicy('https://' + host + '/')
            # Seed acquisition permits www aliases; explicit recovery permissions
            # cover only the named host, including on redirects.
            policy.hosts = {host}
            self.policies[host] = policy
        first = next(iter(self.policies.values()))
        self.seed_url, self.origin = first.seed_url, first.origin

    def validate(self, url: str) -> str:
        try:
            host = (urlsplit(url).hostname or '').lower()
        except ValueError:
            raise ValueError('Malformed candidate URL') from None
        if host not in self.policies:
            raise ValueError('Candidate URL is outside the explicit host permissions')
        return self.policies[host].validate(url)

    @property
    def resolution_evidence(self) -> dict:
        return {host: proof for policy in self.policies.values()
                for host, proof in policy.resolution_evidence.items()}


def capture_references(parent: Corpus, output_dir: Path, selections: list[dict], *,
                       permissions: dict[str, str], scope: str = 'authored') -> dict:
    """Capture only selected reference candidates, with no recursive discovery.

    Selections contain target_id, candidate_url and relation (same_url_recheck or
    candidate_counterpart). Each host requires an explicit processing-rights basis;
    supplied permission text is an operator record, not a verified license grant.
    """
    if type(selections) is not list or not 1 <= len(selections) <= MAX_SELECTIONS:
        raise ValueError('Select 1 through 20 reference candidates')
    if type(permissions) is not dict or not 1 <= len(permissions) <= MAX_SELECTIONS:
        raise ValueError('Explicit host processing permissions are required')
    for host, basis in permissions.items():
        if (type(host) is not str or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                or type(basis) is not str or not basis.strip() or len(basis) > 4096):
            raise ValueError('Invalid host permission record')
    parent_manifest = sha256((parent.root / 'manifest.json').read_bytes())

    def check_parent():
        parent._checked('sources.jsonl')
        parent._checked('references.jsonl')
        if sha256((parent.root / 'manifest.json').read_bytes()) != parent_manifest:
            raise ValueError('Parent corpus changed during reference preflight')

    targets = {row['target_id']: row for row in collect_targets(parent, scope=scope)}
    check_parent()
    plan, seen = [], set()
    for selection in selections:
        if type(selection) is not dict or set(selection) != {'target_id', 'candidate_url', 'relation'}:
            raise ValueError('Selection fields do not match the recovery contract')
        target_id, candidate, relation = (selection[key] for key in ('target_id', 'candidate_url', 'relation'))
        if type(target_id) is not str or target_id not in targets:
            raise ValueError('Recovery target is not in the checked authored reference frontier')
        if type(candidate) is not str or not candidate or len(candidate) > 8192:
            raise ValueError('Use a bounded candidate URL')
        if type(relation) is not str or relation not in {'same_url_recheck', 'candidate_counterpart'}:
            raise ValueError('Unsupported recovery relation')
        if relation == 'same_url_recheck' and candidate != targets[target_id]['url']:
            raise ValueError('A URL recheck must retain the exact original target')
        key = (target_id, candidate)
        if key in seen:
            raise ValueError('Duplicate reference candidate selection')
        seen.add(key)
        plan.append({**selection, 'original_target_url': targets[target_id]['url'],
                     'parent_occurrence_count': targets[target_id]['occurrence_count'],
                     'parent_occurrence_samples': targets[target_id]['occurrence_samples']})
    output = Path(output_dir).expanduser()
    if (not output.is_absolute() or output.exists() or output.is_symlink()
            or not output.parent.is_dir() or output.parent.resolve() != output.parent):
        raise ValueError('Use a fresh absolute snapshot under an existing real parent directory')
    # Validate the entire selection before fetching or creating output state.
    policy = _AllowedPolicies(permissions)
    for item in plan:
        policy.validate(item['candidate_url'])
    check_parent()
    output.mkdir(mode=0o700)
    (output / 'raw').mkdir(mode=0o700)
    capture = _Capture(policy, output, MAX_SELECTIONS, MAX_TOTAL_BYTES)
    snapshot_id = uuid.uuid4().hex
    bindings, interrupted = [], False
    try:
        for item in plan:
            response, row = capture.fetch(item['candidate_url'], purpose='reference_recovery')
            row['id'] = 'recovery:' + snapshot_id + ':' + f'{len(bindings) + 1:06d}'
            context = {'parent_manifest_sha256': parent_manifest, 'target_id': item['target_id'],
                       'original_target_url': item['original_target_url'], 'relation': item['relation'],
                       'assessment': 'unreviewed', 'provenance': 'operator_selected_candidate'}
            row['reference_recovery'] = context
            eligible = False
            if response is not None and response.status == 200:
                if response.body.startswith(b'%PDF-'):
                    row['purpose'] = 'referenced_pdf'
                    eligible = True
                elif row['media_type'] in {'text/html', 'application/xhtml+xml'}:
                    eligible = capture.text(response, row) is not None
                if not eligible:
                    capture.errors.append({'url': item['candidate_url'],
                                           'error': 'Response is not representable HTML or PDF'})
            bindings.append({**item, 'acquisition_id': row['id'], 'status': row['status'],
                             'body_sha256': row['body_sha256'], 'representation_eligible': eligible,
                             'assessment': 'unreviewed', 'historical_equivalence': 'not_established'})
            if capture.exhausted:
                break
    except Exception as error:
        interrupted = True
        capture.errors.append({'error': 'Reference investigation interrupted: ' + type(error).__name__})
    finally:
        _write_private(output / 'acquisitions.jsonl', b''.join(canonical(row) for row in capture.acquisitions))
        _write_private(output / 'documents.jsonl', b'')
    # Recheck checked parent artifacts: concurrent changes remain a warning, never
    # an excuse to silently bind a recovery to a different source generation.
    try:
        check_parent()
        parent_changed = False
    except (OSError, ValueError):
        parent_changed = True
    inventory = {'schema': SCHEMA, 'snapshot_id': snapshot_id, 'captured_at': _utc_now(),
                 'seed_url': parent.manifest['capture_inventory']['seed_url'],
                 'parent_manifest_sha256': parent_manifest, 'document_counts': {},
                 'parent_reference_scope': scope,
                 'rights_basis_by_host': permissions,
                 'rights_scope': 'operator-supplied private processing permission; no license verification',
                 'training_license': 'not established', 'redistribution_license': 'not established',
                 'selection': plan, 'bindings': bindings, 'acquisitions': len(capture.acquisitions),
                 'response_bytes': capture.bytes, 'host_resolution_evidence': policy.resolution_evidence}
    coverage = {'schema': SCHEMA,
                'scope': 'explicitly selected HTTP bodies only; no historical equivalence adjudication',
                'complete_within_declared_scope': not (interrupted or capture.errors or capture.exhausted
                    or parent_changed) and len(bindings) == len(plan)
                    and all(row['representation_eligible'] for row in bindings),
                'errors': capture.errors, 'refusals': capture.refusals,
                'parent_changed': parent_changed, 'bound_reached': capture.exhausted,
                'pending_selections': plan[len(bindings):],
                'unreviewed_original_targets': sorted({row['original_target_url'] for row in plan}),
                'bounds': {'max_selections': MAX_SELECTIONS, 'max_total_bytes': MAX_TOTAL_BYTES},
                'limitations': ['HTTP success does not prove the missing historical work was recovered.',
                                'Candidate provenance does not independently authenticate authorship.',
                                'No recursive link crawl, model inference, training or human approval.']}
    _write_private(output / 'inventory.json', canonical(inventory))
    _write_private(output / 'coverage.json', canonical(coverage))
    return {'output_dir': str(output), 'inventory': inventory, 'coverage': coverage}
