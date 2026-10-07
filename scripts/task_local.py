"""Local trusted task API: python -m scripts.task_local --state PRIVATE_DIR.

Read one JSON request from stdin; write one JSON result. This is not a daemon,
authenticated remote endpoint, chat adapter or autonomous worker. Claim tokens
are local bearer capabilities: keep requests/results private.
"""
import argparse
import json
import sys

from memory.task_dispatch import TaskDispatcher
from memory.task_registry import TaskRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    options = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('Request exceeds 64 KiB')
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {'operation', 'arguments'}:
            raise ValueError('Require operation and arguments')
        if not isinstance(request['arguments'], dict):
            raise ValueError('Arguments must be an object')
        registry = TaskRegistry(options.state)
        operations = {name: getattr(registry, name) for name in
                      ('create', 'get', 'claim', 'checkpoint', 'cancel', 'reclaim', 'submit', 'review')}
        dispatcher = TaskDispatcher(registry)
        operations['read_source'] = dispatcher.read_source
        operations['submit_finding'] = dispatcher.submit_finding
        operation = request['operation']
        if not isinstance(operation, str) or operation not in operations:
            raise ValueError('Unsupported operation')
        result = operations[operation](**request['arguments'])
        print(json.dumps({'ok': True, 'result': result}, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        # Do not echo input, tokens, file contents or arbitrary exception payloads.
        print(json.dumps({'ok': False, 'error': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
