"""Mac Shortcut intake: bounded text on stdin, local queued read-only task only."""
import argparse
import sys
from pathlib import Path

from memory.task_registry import TaskRegistry


def capture(goal: str, state: str, workspace: str) -> dict:
    """Queue a request without executing its text or invoking any agent."""
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4096:
        raise ValueError('Require a nonempty request of at most 4096 characters')
    return TaskRegistry(state).create('rest_proxy', workspace, goal.strip(), [], ['read_source'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default=str(Path.home() / 'Library/Application Support/FIRE/tasks'))
    options = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError('Input too large')
        task = capture(raw.decode('utf-8'), options.state, str(Path(__file__).resolve().parents[1]))
        print('Queued for review. Task ' + task['id'] + '. No agent has been invoked.')
        return 0
    except (ValueError, OSError):
        print('Request could not be queued; check input and local state access.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
