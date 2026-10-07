"""Disposable deterministic task handoff acceptance via separate CLI processes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def run_demo(change_source: bool = False) -> dict:
    """Exercise claims, reopen, evidence, correction and review without inference."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / 'repository'
        workspace.mkdir()
        source = workspace / 'fixture.py'
        source.write_text('def total(values):\n    return sum(values)\n', encoding='utf-8')
        state = root / 'state'
        operations = []

        def call(operation, **arguments):
            response = subprocess.run(
                [sys.executable, '-m', 'scripts.task_local', '--state', str(state)],
                input=json.dumps(dict(operation=operation, arguments=arguments)),
                capture_output=True, text=True, timeout=10)
            payload = json.loads(response.stdout)
            if response.returncode or not payload['ok']:
                raise RuntimeError('Local task operation failed: ' + operation)
            operations.append(operation)
            return payload['result']

        task = call('create', project='fixture', workspace=str(workspace),
                    goal='Locate total implementation', evidence=[], actions=['read_source'])
        identity = dict(project='fixture', task_id=task['id'])
        task = call('claim', **identity, revision=1, worker='deterministic-worker')
        claim = task['claim']['token']
        evidence = call('read_source', **identity, revision=2, claim_token=claim,
                        path='fixture.py', start_line=1, end_line=2)
        task = call('checkpoint', **identity, revision=2, claim_token=claim,
                    checkpoint={'evidence': evidence, 'next_action': 'submit finding'})
        recovered = call('get', **identity)
        if recovered != task:
            raise AssertionError('Process reopen lost checkpoint')
        task = call('submit_finding', **identity, revision=3, claim_token=claim,
                    finding=dict(schema_version=1, answer='total delegates to sum',
                                 citations=[{key: evidence[key] for key in
                                             ('path', 'start_line', 'end_line', 'sha256')}],
                                 limits=['Synthetic fixture; semantic review not established']))
        retained = task['submissions'][0]['finding']['retained_evidence'][0]
        if retained['source'] != evidence['source'] or retained['sha256'] != evidence['sha256']:
            raise AssertionError('Structured submission failed to retain evidence')
        if change_source:
            source.write_text('def total(values):\n    return 0\n', encoding='utf-8')
        current_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        fresh = current_hash == evidence['sha256']
        task = call('review', **identity, revision=4, reviewer='deterministic-reviewer',
                    decision='accept' if fresh else 'request_correction',
                    reason='Fixture source hash matches' if fresh else 'Fixture source changed')
        reopened = call('get', **identity)
        if reopened != task:
            raise AssertionError('Review lost on reopen')
        source.unlink()
        after_removal = call('get', **identity)
        if after_removal['submissions'][0]['finding']['retained_evidence'][0] != retained:
            raise AssertionError('Source removal lost original evidence')
        expected = 'completed' if fresh else 'queued'
        if task['status'] != expected or len(task['submissions']) != 1:
            raise AssertionError('Unexpected handoff outcome')
        return dict(schema_version=1, fixture='synthetic Python source',
                    operations=operations, operation_count=len(operations),
                    separate_process_per_operation=True, checkpoint_reopened=True,
                    review_reopened=True, source_hash_matches=fresh, status=task['status'],
                    original_finding_retained=True, structured_submission=True,
                    retained_evidence_after_source_removal=True,
                    reviewer='deterministic fixture, not independent reasoning or authentication',
                    inference_used=False, live_rental_data_used=False,
                    token_savings_measured=False)


if __name__ == '__main__':
    print(json.dumps({'unchanged_source': run_demo(), 'changed_source': run_demo(True)}, indent=2))
