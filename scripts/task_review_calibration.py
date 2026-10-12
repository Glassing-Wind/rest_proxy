"""Emit synthetic review prompts without the grading key; no model or task calls."""
import argparse
import json
from pathlib import Path

from memory.fire_store import encoded
from scripts.task_review_export import REVIEW_INSTRUCTIONS


def calibration_prompt(fixture: dict, variant: str) -> dict:
    """Whitelist model inputs so grading metadata never enters the review prompt."""
    if variant not in ('full', 'missing-definition'):
        raise ValueError('Unknown variant')
    sources = fixture['source']
    if variant == 'missing-definition':
        sources = [item for item in sources if item['path'] != 'fixture/registry.py']
    prompt = dict(instructions=REVIEW_INSTRUCTIONS, finding=fixture['finding'],
                  source=[dict(path=item['path'], source=item['source']) for item in sources])
    if len(encoded(prompt).encode('utf-8')) > 8192:
        raise ValueError('Calibration prompt exceeds 8 KiB')
    return prompt


def citation_excerpt(prompt: dict, path: str, start: int, end: int) -> dict:
    """Resolve numbered fixture lines for human review; never judge claim support."""
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        raise ValueError('Invalid citation range')
    if end - start >= 100:
        raise ValueError('Citation exceeds 100 lines')
    matches = [item for item in prompt['source'] if item['path'] == path]
    if len(matches) != 1:
        raise ValueError('Citation path absent or ambiguous')
    lines = {}
    for line in matches[0]['source'].splitlines():
        number, separator, text = line.partition(': ')
        if not separator or not number.isdigit() or int(number) in lines:
            raise ValueError('Invalid numbered source')
        lines[int(number)] = text
    if any(number not in lines for number in range(start, end + 1)):
        raise ValueError('Citation line missing')
    return dict(path=path, start_line=start, end_line=end,
                source='\n'.join(f'{number}: {lines[number]}' for number in range(start, end + 1)),
                validation='range exists only; semantic support requires review')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--variant', choices=['full', 'missing-definition'], required=True)
    options = parser.parse_args()
    with Path(options.fixture).open('rb') as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError('Fixture exceeds 64 KiB')
    print(json.dumps(calibration_prompt(json.loads(raw), options.variant), ensure_ascii=False))


if __name__ == '__main__':
    main()
