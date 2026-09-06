#!/usr/bin/env python3
"""Replay source reads for all pilot cases; this is NOT an agent benchmark.

Measures client wall time and returned UTF-8 bytes. No model is called, so model
usage and answer correctness are unavailable. Uses known evidence paths on purpose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from run_mcp_investigation_pass import _initialize_session, _mcp_call

ROOT = Path(__file__).resolve().parents[1]
NATIVE_READER = '''import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_bytes().decode('utf-8')
lines=text.split('\\n')
if lines and lines[-1]=='': lines.pop()
print('\\n'.join(f'{i}: {line.removesuffix(chr(13))}' for i,line in enumerate(lines[:80],1)))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    session = _initialize_session()
    setup_seconds = time.perf_counter() - started
    cases = json.loads((ROOT / 'benchmarks/agent_tooling_cases.json').read_text())['cases']
    rows = []
    for index, case in enumerate(cases):
        relative = case['expected_evidence'][0]
        if relative == 'tools/brain/code_intel':
            relative = 'tools/brain/code_intel/core.py'
        path = ROOT / relative
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        results = {}
        # Alternate order to reduce a systematic first-read filesystem-cache bias.
        order = ['native', 'mcp'] if index % 2 == 0 else ['mcp', 'native']
        for condition in order:
            started = time.perf_counter()
            if condition == 'native':
                output = subprocess.check_output([sys.executable, '-c', NATIVE_READER, str(path)], text=True)
            else:
                output = _mcp_call(session, 'describe_file', {
                    'project_path': str(ROOT), 'file_path': relative,
                    'include_source': True, 'start_line': 1, 'max_lines': 80,
                    'max_chars': 20000,
                }, str(ROOT))
            results[condition] = {'seconds': time.perf_counter() - started,
                                  'returned_utf8_bytes': len(output.encode('utf-8')), 'output': output}
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        source_lines = results['native']['output'].rstrip('\n')
        valid = before == after and before in results['mcp']['output'] and source_lines in results['mcp']['output']
        rows.append({'case_id': case['id'], 'file': relative, 'sha256': before,
                     'order': order, 'source_matches': valid, **results})
    report = {
        'kind': 'known-file source replay, not agent benchmark',
        'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'dirty_worktree': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
        'index_dependency': 'none: both conditions read current local files',
        'mcp_session_setup_seconds': setup_seconds,
        'measurement': 'client wall time per tool operation; native subprocess versus MCP HTTP round trip',
        'model_input_tokens': None, 'model_output_tokens': None, 'answer_correctness': None,
        'limitations': ['Known evidence paths supplied; no discovery or agent reasoning measured.',
                        'One pair per case; timing is descriptive, not statistically conclusive.',
                        'Returned bytes include MCP citation metadata; not model token usage.'],
        'rows': rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    passed = sum(row['source_matches'] for row in rows)
    print(f'Source equality and hash verification: {passed}/{len(rows)}')
    if passed != len(rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
