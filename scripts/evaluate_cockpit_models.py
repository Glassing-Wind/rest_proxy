"""Run bounded synthetic cockpit comparisons against an explicit local endpoint or native findings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphrag_core.cockpit_eval import (
    DEFAULT_PLAN, MAX_INPUT_BYTES, TrialError, evaluate_external, load_cases,
    run_trials, write_prompt_package,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'package', 'evaluate'):
        command = commands.add_parser(name)
        command.add_argument('--plan', type=Path, default=DEFAULT_PLAN)
        command.add_argument('--output', type=Path, required=True, help='Fresh private output directory; parent must exist')
        if name == 'run':
            command.add_argument('--adapter', choices=['loopback_openai_chat', 'loopback_lmstudio_reasoning_off'],
                                 default='loopback_openai_chat')
            command.add_argument('--base-url', required=True)
            command.add_argument('--model', required=True)
            command.add_argument('--max-output-tokens', type=int, default=2048)
            command.add_argument('--timeout-seconds', type=float, default=90)
        if name == 'evaluate':
            command.add_argument('--input', type=Path, required=True, help='Explicit native findings JSON contract')
            command.add_argument('--model', help='Unverified operator label, never asserted as runtime identity')
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.plan)
        if args.command == 'run':
            result = run_trials(cases, base_url=args.base_url, model=args.model, output=args.output,
                                max_output_tokens=args.max_output_tokens, timeout_seconds=args.timeout_seconds,
                                adapter=args.adapter)
        elif args.command == 'package':
            result = write_prompt_package(cases, args.output)
        else:
            with args.input.open('rb') as stream:
                supplied = stream.read(MAX_INPUT_BYTES + 1)
            result = evaluate_external(cases, supplied, args.output, model=args.model)
    except TrialError as exc:
        print(json.dumps({'error_stage': exc.stage}))
        return 2
    except OSError:
        print(json.dumps({'error_stage': 'local_io'}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
