"""One opt-in disposable model call plus pinned fixture checks; no human wording gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx

from memory.task_provider import LocalTaskProvider
from memory.task_review import finding_sha256
from memory.task_review_fixture import SUM_GOAL, SUM_SOURCE


def check_fixture(endpoint: str, model: str, max_tokens: int = 4096) -> dict:
    """Retain private state and a sanitized receipt on either success or failure."""
    LocalTaskProvider(endpoint, model, enabled=True, max_tokens=max_tokens)
    root = Path(tempfile.mkdtemp(prefix='fire-provider-fixture-'))
    os.chmod(root, 0o700)
    workspace = root / 'workspace'
    workspace.mkdir(mode=0o700)
    (workspace / 'fixture.py').write_text(SUM_SOURCE)
    state = root / 'state'
    state.mkdir(mode=0o700)
    cwd = Path(__file__).resolve().parents[1]
    command = [sys.executable, '-m', 'scripts.task_local', '--state', str(state)]
    receipt = dict(schema_version=1, status='failed', private_directory=str(root),
                   model=model, endpoint=endpoint, max_tokens=max_tokens,
                   python=sys.version, httpx=httpx.__version__, inference_invocations=0,
                   started_at=time.time(), human_review=False,
                   implementation_sha256={name: hashlib.sha256((cwd / name).read_bytes()).hexdigest()
                       for name in ('memory/task_review.py', 'memory/task_review_fixture.py',
                                    'memory/task_provider.py', 'scripts/task_run.py',
                                    'scripts/check_task_provider_fixture.py')})

    def local(operation, arguments):
        result = subprocess.run(command, cwd=cwd, input=json.dumps(dict(operation=operation, arguments=arguments)),
                                text=True, capture_output=True, timeout=10)
        data = json.loads(result.stdout)
        if result.returncode or data.get('ok') is not True:
            raise ValueError('Local fixture operation rejected')
        return data['result']

    started = time.monotonic()
    stage = 'create'
    try:
        task = local('create', dict(project='fixture', workspace=str(workspace), goal=SUM_GOAL,
                                    evidence=[], actions=['read_source']))
        identity = dict(project='fixture', task_id=task['id'])
        receipt['task_id'] = task['id']
        stage = 'generation'
        run = [sys.executable, '-m', 'scripts.task_run', '--state', str(state), '--project', 'fixture',
               '--task-id', task['id'], '--revision', '1', '--path', 'fixture.py',
               '--start-line', '1', '--end-line', '2', '--endpoint', endpoint, '--model', model,
               '--max-tokens', str(max_tokens), '--execute']
        receipt['inference_invocations'] = 1
        output = subprocess.run(run, cwd=cwd, text=True, capture_output=True, timeout=45)
        if output.returncode:
            # task_run's stable diagnostics exclude raw provider bodies and capabilities.
            receipt['generation_failure'] = json.loads(output.stderr)
            raise ValueError('Fixture generation failed')
        result = json.loads(output.stdout)
        receipt['result'] = result
        stage = 'pre_review_reopen'
        retained = local('get', identity)
        original = retained['submissions'][-1]
        if (retained['status'] != 'review_pending' or original['finding'] != result['finding'] or
                original['generation'] != result['generation']):
            raise ValueError('Fixture generation was not retained on reopen')
        stage = 'fixture_review'
        digest = finding_sha256(original['finding'])
        reviewed = local('review_sum_fixture', dict(identity, revision=retained['revision'],
                                                    submission=1, finding_sha256=digest))
        stage = 'post_review_reopen'
        reopened = local('get', identity)
        if (reopened != reviewed or reopened['status'] != 'completed' or
                reopened['submissions'] != retained['submissions']):
            raise ValueError('Fixture review or original evidence was not retained')
        receipt.update(status='passed', generation=result['generation'],
                       reopen=dict(separate_processes=True, revision=reopened['revision'],
                                   status=reopened['status'], original_submissions_equal=True,
                                   review_equal=True), review=reopened['reviews'][-1])
    except Exception as error:
        receipt.update(error_type=type(error).__name__, failure_stage=stage,
                       retry_performed=False, remote_termination='unknown')
    receipt['elapsed_seconds'] = round(time.monotonic() - started, 6)
    target = root / 'receipt.json'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--max-tokens', type=int, default=4096)
    parser.add_argument('--execute', action='store_true')
    options = parser.parse_args()
    if not options.execute:
        parser.error('Require --execute for one disposable provider call')
    try:
        receipt = check_fixture(options.endpoint, options.model, options.max_tokens)
        print(json.dumps(receipt, allow_nan=False))
        return 0 if receipt['status'] == 'passed' else 1
    except Exception as error:
        print(json.dumps(dict(status='failed', error_type=type(error).__name__)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
