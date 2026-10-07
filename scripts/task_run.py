"""Explicit one-task loopback dispatch; no polling, retries or automatic review."""
import argparse
import asyncio
import json
import sys

from memory.task_provider import LocalTaskProvider
from memory.task_registry import TaskRegistry
from memory.task_worker import run_worker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--revision', required=True, type=int)
    parser.add_argument('--path', required=True)
    parser.add_argument('--start-line', type=int, default=1)
    parser.add_argument('--end-line', type=int, default=80)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--additional-source', action='append', default=[],
                        help='JSON object with path/start_line/end_line (at most two)')
    parser.add_argument('--execute', action='store_true')
    options = parser.parse_args()
    if not options.execute:
        parser.error('Require --execute to claim a task and contact the provider')
    provider = None
    try:
        additional = [json.loads(value) for value in options.additional_source]
        provider = LocalTaskProvider(options.endpoint, options.model, enabled=True,
                                     max_tokens=options.max_tokens)
        result = asyncio.run(run_worker(
            TaskRegistry(options.state), options.project, options.task_id,
            options.revision, 'local-provider', options.path, provider, enabled=True,
            start_line=options.start_line, end_line=options.end_line, additional_sources=additional))
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as error:
        # Do not echo exception messages, bearer tokens, HTTP bodies or model output.
        print(json.dumps(dict(ok=False, error_type=type(error).__name__,
                              validation_stage=getattr(error, 'stage', None),
                              attempt=provider.attempt_receipt if provider else None)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
