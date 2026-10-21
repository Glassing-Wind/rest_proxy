"""A pinned synthetic fixture with mechanical acceptance, never arbitrary code review."""
import hashlib


SUM_SOURCE = 'def total(values):\n    return sum(values)\n'
SUM_SOURCE_SHA256 = '3251dbf18617cd67c47707cb9bd1611dbac212ff0ee9814a75b8ce1f34b85aad'
SUM_ANSWER = 'The function total(values) returns the sum of the elements in the values iterable.'
SUM_LIMIT = 'Only fixture.py lines 1-2 were inspected.'
SUM_GOAL = (f'Describe fixture.py lines 1-2. Return answer exactly "{SUM_ANSWER}" '
            f'and limits exactly ["{SUM_LIMIT}"]. Copy the supplied citation exactly.')


def verify_sum_fixture(task: dict, finding: dict, evidence: list[dict]) -> list[dict]:
    """Check fixed source, exact output and known behavior without running supplied code."""
    citation = dict(path='fixture.py', start_line=1, end_line=2, sha256=SUM_SOURCE_SHA256)
    if (task['goal'] != SUM_GOAL or finding['citations'] != [citation] or
            finding['answer'] != SUM_ANSWER or finding['limits'] != [SUM_LIMIT]):
        raise ValueError('Finding does not match the pinned synthetic fixture contract')
    if (len(evidence) != 1 or evidence[0]['sha256'] != SUM_SOURCE_SHA256 or
            evidence[0]['source'] != '1: def total(values):\n2:     return sum(values)' or
            evidence[0]['total_lines'] != 2 or
            hashlib.sha256(SUM_SOURCE.encode()).hexdigest() != SUM_SOURCE_SHA256):
        raise ValueError('Source is not the exact synthetic sum fixture')
    # Execute only this application-owned literal after its pinned hash check.
    # The caller's file contents, goal, answer and judgments are never executed.
    namespace = {'__builtins__': {'sum': sum}}
    exec(compile(SUM_SOURCE, '<pinned-sum-fixture-v1>', 'exec'), namespace)
    cases = [([], 0), ([1, 2, 3], 6), ([-4, 1], -3), ([1.5, 0.5], 2.0)]
    results = []
    for values, expected in cases:
        actual = namespace['total'](values)
        if actual != expected:
            raise ValueError('Synthetic fixture behavior assertion failed')
        results.append(dict(values=values, expected=expected, actual=actual))
    return results
