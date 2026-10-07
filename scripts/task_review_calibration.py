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
