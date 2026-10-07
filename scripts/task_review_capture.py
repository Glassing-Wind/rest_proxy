"""Import advisory text against an exact exported bundle; no cloud calls or approval."""
import argparse
import json
from pathlib import Path
import sys

from memory.fire_store import encoded
from memory.task_registry import TaskRegistry
from scripts.task_review_export import review_bundle


def capture_assessment(registry: TaskRegistry, project: str, bundle: dict, text: str) -> dict:
    """Reject edited/stale exports; persist advice separately from task decisions."""
    task = registry.get(project, bundle['task_id'])
    expected = review_bundle(task, bundle['submission'])
    if encoded(bundle) != encoded(expected):
        raise ValueError('Export no longer matches the current task revision and finding')
    return registry.record_assessment(project, task['id'], bundle['revision'],
                                      bundle['submission'], 'apple-shortcut', text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--bundle', required=True)
    options = parser.parse_args()
    try:
        with Path(options.bundle).open('rb') as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError('Export exceeds 8 KiB')
        assessment = sys.stdin.buffer.read(8193)
        if len(assessment) > 8192:
            raise ValueError('Assessment exceeds 8 KiB')
        task = capture_assessment(TaskRegistry(options.state), options.project,
                                  json.loads(raw), assessment.decode('utf-8'))
        print('Advisory assessment saved. Task status remains ' + task['status'] + '.')
        return 0
    except (ValueError, TypeError, KeyError, OSError):
        print('Assessment not saved: invalid input or stale evidence bundle.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
