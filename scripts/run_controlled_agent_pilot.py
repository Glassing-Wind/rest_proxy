#!/usr/bin/env python3
"""Fresh-context native versus MCP+native investigation pilot (no live calls by default)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time

import run_mcp_investigation_pass as mcp

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ['get_indexing_health', 'get_mcp_tool_catalog', 'get_project_overview',
         'search_codebase', 'get_symbol_context', 'get_call_chain', 'find_references',
         'describe_file', 'grep_codebase', 'list_symbol_matches', 'find_definitions',
         'get_directory_snapshot', 'trace_symbol_cross_project', 'get_test_coverage_for',
         'trace_graph_provenance']
FIELDS = ('Structural status', 'Structural active run', 'Structural last success',
          'Semantic status', 'Semantic active run', 'Semantic last success',
          'Semantic target struct', 'Semantic active struct')


def default_codex():
    """Find the bundled CLI across desktop layouts, then fall back to PATH."""
    for path in ('/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex',
                 '/Applications/ChatGPT.app/Contents/Resources/codex'):
        if Path(path).is_file():
            return path
    return shutil.which('codex') or 'codex'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_identity(root):
    paths = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root
    ).decode().split('\0')
    records = {}
    for name in sorted(set(paths)):
        if not name or name.startswith(('.runtime/', 'benchmarks/reports/')):
            continue
        path = root / name
        if path.is_file():
            records[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.exists():
            records[name] = 'deleted'
    return {'sha256': digest(records), 'files': records}


def health_identity(raw):
    fields = {}
    for line in raw.splitlines():
        for label in FIELDS:
            if label + ':' in line:
                fields[label] = line.split(label + ':', 1)[1].strip().strip('`')
        if any(word in line.lower() for word in ('sync', 'alignment:', 'aligned', 'stale', 'missing from index')):
            fields['status:' + line.strip()] = True
    if any(label not in fields for label in FIELDS):
        raise ValueError('Health response missing required run identity fields')
    # Include coverage and shadow counts, not just run IDs: equal timestamps do
    # not prove an uncontaminated or complete index.
    return {'sha256': digest(raw), 'fields': fields, 'raw': raw}


def snapshot(root, url):
    mcp.MCP_URL = url
    session = mcp._initialize_session()
    try:
        raw = mcp._mcp_call(session, 'get_indexing_health', {'workspace_id': str(root)}, str(root))
    finally:
        mcp._request(url, method='DELETE', headers={'Mcp-Session-Id': session,
                     'MCP-Protocol-Version': mcp.PROTOCOL_VERSION})
    return {'source': source_identity(root), 'index': health_identity(raw)}


def identity_equal(a, b):
    return all(a[k]['sha256'] == b[k]['sha256'] for k in ('source', 'index'))


def summarize_events(raw, condition, requested_model=None):
    events = []
    malformed = 0
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            malformed += 1
    turns = [e for e in events if e.get('type') == 'turn.completed']
    usage = None
    if turns and all(isinstance(e.get('usage'), dict) for e in turns):
        keys = set().union(*(e['usage'] for e in turns))
        usage = {k: sum(e['usage'].get(k, 0) for e in turns) for k in keys}
    items = [e.get('item', {}) for e in events if e.get('type') == 'item.completed']
    calls = [i for i in items if i.get('type') == 'mcp_tool_call']
    successful_calls = [i for i in calls if i.get('status') == 'completed'
                        and not i.get('error') and i.get('result') is not None
                        and not i['result'].get('isError', False)]
    violations = [i for i in items if i.get('type') in ('file_change', 'web_search')]
    violations += [i for i in calls if condition == 'native' or i.get('server') != 'graphrag' or i.get('tool') not in TOOLS]
    if condition == 'mcp' and not any(i.get('tool') == 'get_indexing_health'
                                    for i in successful_calls):
        violations.append({'type': 'mcp_availability_unverified'})
    observed_models = sorted({e['model'] for e in events if isinstance(e.get('model'), str)})
    if requested_model and any(model != requested_model for model in observed_models):
        violations.append({'type': 'model_mismatch', 'observed': observed_models})
    # These references require manual exclusion review; conservatively invalidate the run.
    forbidden = ('benchmarks/reports/', 'agent_tooling_cases.json', 'grading-key.json',
                 'native-transcript.json', 'mcp-evidence-log.json')
    contamination = sorted({term for term in forbidden if term in raw})
    if contamination:
        violations.append({'type': 'possible_answer_contamination', 'paths': contamination})
    return {'observed_models': observed_models,
            'model_verification': 'observed_event' if observed_models else 'requested_only_unverified',
            'usage': usage, 'completed_turns': len(turns), 'malformed_lines': malformed,
            'command_calls': sum(i.get('type') == 'command_execution' for i in items),
            'mcp_calls': len(calls), 'successful_mcp_calls': len(successful_calls),
            'policy_violations': violations,
            'errors': [e for e in events if e.get('type') in ('error', 'turn.failed')]}


def command(args, condition, answer):
    cmd = [args.codex, '--no-daemon', 'exec', '--strict-config',
           '--ignore-user-config', '--ephemeral', '--json',
           '--sandbox', 'read-only', '--skip-git-repo-check', '-C', str(args.root),
           '-m', args.model, '-c', 'model_reasoning_effort=' + json.dumps(args.effort),
           '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
           '-o', str(answer)]
    if condition == 'mcp':
        cmd += ['-c', 'mcp_optional_startup_grace_ms=0']
        for setting in ('url=' + json.dumps(args.mcp_url), 'enabled=true', 'required=true',
                        'startup_readiness="catalog"',
                        'enabled_tools=' + json.dumps(TOOLS)):
            cmd += ['-c', 'mcp_servers.graphrag.' + setting]
        # These read-only tools are explicitly authorized for unattended evaluation.
        # Keep approval policy "never"; do not grant access to write/admin tools.
        for tool in TOOLS:
            cmd += ['-c', f'mcp_servers.graphrag.tools.{tool}.approval_mode="approve"']
    return cmd + ['-']


def prompt_for(case, condition, root):
    tools = ('Use native file/search tools only. Do not access MCP, database services, or network.'
             if condition == 'native' else
             'First discover the graphrag MCP tools using available tool-search/discovery if needed, '
             'then invoke GraphRAG MCP get_indexing_health for the repository to verify tool '
             'availability. If unavailable, stop and report that setup failure. Then use '
             'GraphRAG MCP tools and native file/search tools as helpful for the question.')
    return (f'Investigate the repository at {root}. {tools}\n'
            'Read-only investigation: do not edit files, run indexing, install dependencies, '
            'spawn agents, or execute tests or application code. Do not read benchmark cases, '
            'reports, prior answers, agent history, credentials, or files outside the repository. '
            'Use only source and project documentation as evidence. Explain the answer with '
            'specific file/line citations and identify uncertainty. Do not grade yourself.\n\n'
            + case['prompt'])


def execute_agent(args, condition, run, prompt):
    """Run one bounded fresh session and preserve its raw evidence."""
    (run / 'prompt.txt').write_text(prompt)
    cmd = command(args, condition, run / 'answer.md')
    start = time.monotonic()
    timed_out = False
    with (run / 'events.jsonl').open('w') as out, (run / 'stderr.txt').open('w') as err:
        child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True,
                                 start_new_session=True)
        try:
            child.communicate(prompt, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            timed_out = True
    elapsed = time.monotonic() - start
    return child.returncode, timed_out, elapsed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--codex', default=default_codex())
    p.add_argument('--model', default='gpt-6-astra')
    p.add_argument('--effort', default='low')
    p.add_argument('--mcp-url', default='http://127.0.0.1:8001/mcp')
    p.add_argument('--cases', type=Path, default=ROOT / 'benchmarks/agent_tooling_cases.json')
    p.add_argument('--case-id', action='append')
    p.add_argument('--output', type=Path, required=True, help='New directory OUTSIDE repository')
    p.add_argument('--seed', type=int, default=29)
    p.add_argument('--timeout', type=int, default=600)
    p.add_argument('--index-state', choices=('fresh', 'warm', 'stale', 'partial'), default='warm')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--preflight-only', action='store_true',
                   help='With --execute, verify child MCP access without benchmark questions')
    args = p.parse_args()
    args.root = args.root.resolve()
    args.output = args.output.resolve()
    if args.output == args.root or args.root in args.output.parents:
        p.error('Output must be outside repository to avoid contamination and fingerprint drift')
    cases = json.loads(args.cases.read_text())['cases']
    selected = args.case_id or ['http_mcp_startup', 'index_job_recovery', 'symbol_context', 'semantic_search_ranking']
    cases = [next(c for c in cases if c['id'] == name) for name in selected]
    rng = random.Random(args.seed)
    rng.shuffle(cases)
    schedule = [(c, condition) for i, c in enumerate(cases)
                for condition in (('native', 'mcp') if i % 2 == 0 else ('mcp', 'native'))]
    if not args.execute:
        print(json.dumps({'execute': False, 'model': args.model, 'effort': args.effort,
                          'schedule': [(c['id'], t) for c, t in schedule]}, indent=2))
        return 0
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    # A local project config can reintroduce tools even when user config is ignored.
    ignored_user_config = (Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
                           / 'config.toml').resolve()
    if any((path / '.codex/config.toml').exists()
           and (path / '.codex/config.toml').resolve() != ignored_user_config
           for path in [args.root, *args.root.parents]):
        raise RuntimeError('Project/ancestor Codex config found; isolation needs manual review')
    preflight = args.output / 'preflight'
    preflight.mkdir(mode=0o700)
    probe = (f'Discover the graphrag MCP tools using available tool-search/discovery if needed, '
             f'then invoke GraphRAG MCP get_indexing_health for {args.root}. '
             'Do not use shell, edit files, or access other services. '
             'If discovery cannot find the tool, report that failure.')
    returncode, timed_out, elapsed = execute_agent(args, 'mcp', preflight, probe)
    metrics = summarize_events((preflight / 'events.jsonl').read_text(), 'mcp', args.model)
    passed = (returncode == 0 and not timed_out and metrics['usage'] is not None
              and not metrics['policy_violations'] and not metrics['errors']
              and metrics['malformed_lines'] == 0 and metrics['command_calls'] == 0)
    result = {'cli_version': subprocess.check_output([args.codex, '--version']).decode().strip(),
              'passed': passed, 'exit_code': returncode, 'seconds': elapsed,
              'timed_out': timed_out, **metrics}
    (preflight / 'record.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'preflight': result}), flush=True)
    if not passed or args.preflight_only:
        return 0 if passed else 1
    baseline = snapshot(args.root, args.mcp_url)
    (args.output / 'baseline.json').write_text(json.dumps(baseline, indent=2))
    if args.index_state in ('fresh', 'warm') and not all(text in baseline['index']['raw'] for text in (
        '**Sync Status**: ✅ Healthy', '**Run Alignment**:        ✅ Aligned',
    )):
        raise RuntimeError('Fresh/warm experiment requires initially healthy aligned index')
    report = {'schema_version': 1, 'model': args.model, 'effort': args.effort,
              'index_state': args.index_state,
              'seed': args.seed, 'runs': [], 'correctness': None,
              'limitations': ['Shell/network restriction is prompt policy, not full tool isolation.',
                              'AGENTS.md, system skills, and managed configuration may be inherited.',
                              'Small observational pilot; no statistical superiority claim.']}
    report['cli_version'] = subprocess.check_output([args.codex, '--version']).decode().strip()
    for number, (case, condition) in enumerate(schedule):
        run = args.output / f'{number:02d}-{case["id"]}-{condition}'
        run.mkdir(mode=0o700)
        before = snapshot(args.root, args.mcp_url)
        if not identity_equal(baseline, before):
            raise RuntimeError('Source/index drift before run; aborting')
        prompt = prompt_for(case, condition, args.root)
        returncode, timed_out, elapsed = execute_agent(args, condition, run, prompt)
        after = snapshot(args.root, args.mcp_url)
        metrics = summarize_events((run / 'events.jsonl').read_text(), condition, args.model)
        valid = (identity_equal(before, after) and returncode == 0 and not timed_out
                 and metrics['usage'] is not None and not metrics['policy_violations']
                 and not metrics['errors'] and metrics['malformed_lines'] == 0)
        row = {'case_id': case['id'], 'condition': condition, 'seconds': elapsed,
               'exit_code': returncode, 'timed_out': timed_out, 'valid': valid,
               'correctness': None, **metrics}
        (run / 'record.json').write_text(json.dumps({'result': row, 'before': before, 'after': after}, indent=2))
        report['runs'].append(row)
        (args.output / 'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(row), flush=True)
        if not valid:
            return 1
    # Random opaque labels; key is separate from the blind review packet.
    candidates = list(args.output.glob('[0-9][0-9]-*/answer.md'))
    rng.shuffle(candidates)
    blind = args.output / 'blind'
    blind.mkdir(mode=0o700)
    key = {}
    for i, candidate in enumerate(candidates):
        label = f'answer-{i + 1:03d}'
        key[label] = candidate.parent.name
        case_id = candidate.parent.name[3:].rsplit('-', 1)[0]
        question = next(c['prompt'] for c in cases if c['id'] == case_id)
        (blind / (label + '.md')).write_text('Question: ' + question + '\n\n' + candidate.read_text())
    (args.output / 'grading-key.json').write_text(json.dumps(key, indent=2))
    (blind / 'instructions.md').write_text(
        'Independently verify each answer against source. Do not inspect sibling artifacts or grading key. '
        'Grade correctness, completeness, citation validity, and unsupported claims; record evidence and '
        'uncertainty. No condition, time, or token metrics are supplied. Answers may reveal tool provenance, '
        'so blinding is partial. Correctness remains null until this separate review is completed.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
