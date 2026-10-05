"""Conservative static file relationships with explicit source-binding rules.

These are source candidates, not runtime dispatch guarantees. Python package search
uses the snapshot root only; dynamic/reexport/class/nested binding resolution is absent.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import hashlib
import json
import posixpath
import sys


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class Bindings(ast.NodeVisitor):
    def __init__(self):
        self.names = Counter()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names[node.id] += 1

    def visit_FunctionDef(self, node):
        self.names[node.name] += 1
        for expression in [*node.decorator_list, *node.args.defaults, *filter(None, node.args.kw_defaults)]:
            self.visit(expression)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.names[node.name] += 1
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)

    def visit_Import(self, node):
        for alias in node.names:
            self.names[alias.asname or alias.name.split('.')[0]] += 1

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.names[alias.asname or alias.name] += 1

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names[node.name] += 1
        self.generic_visit(node)


class Calls(ast.NodeVisitor):
    def __init__(self):
        self.calls = []

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef
    visit_ListComp = visit_FunctionDef
    visit_SetComp = visit_FunctionDef
    visit_DictComp = visit_FunctionDef
    visit_GeneratorExp = visit_FunctionDef


def python_module(path):
    if not path.endswith('.py'):
        return None
    parts = path[:-3].split('/')
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def build_relationships(files: list[dict]) -> list[dict]:
    modules = defaultdict(list)
    infos = {}
    by_path = {file['path']: file for file in files}
    links = {}

    def emit(kind, source, target, *, rule, line=None, caller=None, callee=None, expression=None, start_byte=None, end_byte=None):
        payload = {'kind': kind, 'source_file': source['path'], 'target_file': target['path'],
                   'source_sha256': source['sha256'], 'target_sha256': target['sha256'],
                   'rule': rule, 'line': line, 'caller_id': caller, 'callee_id': callee,
                   'expression': expression, 'start_byte': start_byte, 'end_byte': end_byte,
                   'semantics': 'static-source-candidate'}
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        links[digest] = {'id': digest, 'source_id': source['id'], 'target_id': target['id'],
                         'kind': kind, 'payload_json': canonical(payload)}

    for file in files:
        module = python_module(file['path'])
        if module is not None and file['language'] == 'python':
            modules[module].append(file)
            try:
                tree = ast.parse(file['content'])
            except SyntaxError:
                # Native parsers may accept syntax newer than the running interpreter.
                # Preserve source/facts; leave this module's relationships unresolved.
                continue
            bindings = Bindings()
            bindings.visit(tree)
            globals_ = {name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names}
            wildcard = bool(bindings.names['*'])
            dynamic = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                          and node.func.id in {'exec', 'eval', 'globals', 'locals', 'vars', 'setattr', 'delattr'}
                          for node in ast.walk(tree))
            defs = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.decorator_list:
                    matches = [symbol for symbol in file['symbols'] if symbol['name'] == node.name
                               and symbol['start'] == node.lineno and symbol['kind'] == 'Function']
                    if len(matches) == 1 and bindings.names[node.name] == 1 and node.name not in globals_ and not wildcard and not dynamic:
                        defs[node.name] = matches[0]
            infos[file['path']] = {'tree': tree, 'bindings': bindings.names, 'defs': defs,
                                   'globals': globals_, 'wildcard': wildcard, 'dynamic': dynamic,
                                   'modified_bindings': {node.value.id for node in ast.walk(tree)
                                       if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
                                       and isinstance(node.value, ast.Name)}}

    def module_file(name):
        if not name or name.split('.')[0] in sys.stdlib_module_names:
            return None
        matches = modules.get(name, [])
        return matches[0] if len(matches) == 1 else None

    def import_module(file, node):
        if not node.level:
            return node.module or ''
        package = file['path'].split('/')[:-1]
        if node.level > len(package):
            return ''
        base = package[:len(package) - node.level + 1]
        return '.'.join(base + ((node.module or '').split('.') if node.module else []))

    # Known writes through directly imported module aliases invalidate those exports.
    blocked_exports = set()
    for file in files:
        info = infos.get(file['path'])
        if not info:
            continue
        aliases = {}
        for node in info['tree'].body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname or '.' not in alias.name:
                        target = module_file(alias.name)
                        if target:
                            aliases[alias.asname or alias.name] = target['path']
        for node in ast.walk(info['tree']):
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
                    and isinstance(node.value, ast.Name) and node.value.id in aliases):
                blocked_exports.add((aliases[node.value.id], node.attr))

    for file in files:
        facts = json.loads(file['facts_json'])
        info = infos.get(file['path'])
        if info:
            imported = {}
            declared_at = {node.name: (node.end_lineno, node.end_col_offset) for node in info['tree'].body
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for node in info['tree'].body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = module_file(alias.name)
                        if target:
                            emit('imports', file, target, rule='python-root-module', line=node.lineno,
                                 expression=alias.name)
                            local = alias.asname or alias.name.split('.')[0]
                            if alias.asname or '.' not in alias.name:
                                imported[local] = (target, None)
                                declared_at[local] = (node.end_lineno, node.end_col_offset)
                elif isinstance(node, ast.ImportFrom):
                    target = module_file(import_module(file, node))
                    if target:
                        emit('imports', file, target, rule='python-root-module', line=node.lineno,
                             expression=('.' * node.level) + (node.module or ''))
                        for alias in node.names:
                            if alias.name != '*':
                                imported[alias.asname or alias.name] = (target, alias.name)
                                declared_at[alias.asname or alias.name] = (node.end_lineno, node.end_col_offset)
            scopes = [(None, info['tree'].body)] + [(node, node.body) for node in info['tree'].body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            line_offsets = [0]
            raw = file['content'].encode()
            line_offsets.extend(i + 1 for i, byte in enumerate(raw) if byte == 10)
            observations = {(call['start_byte'], call['end_byte']): call for call in facts['calls']}
            for function, statements in scopes:
                locals_ = Bindings()
                visitor = Calls()
                for statement in statements:
                    locals_.visit(statement)
                    visitor.visit(statement)
                if function:
                    args = function.args
                    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                                *([args.vararg] if args.vararg else []), *([args.kwarg] if args.kwarg else [])]:
                        locals_.names[arg.arg] += 1
                    for parameter in getattr(function, 'type_params', []):
                        locals_.names[parameter.name] += 1
                for call in visitor.calls:
                    observation = observations.get((line_offsets[call.lineno - 1] + call.col_offset,
                                                    line_offsets[call.end_lineno - 1] + call.end_col_offset))
                    if not observation or info['wildcard'] or info['dynamic']:
                        continue
                    target = file
                    name = None
                    binding = None
                    if isinstance(call.func, ast.Name):
                        binding = call.func.id
                        name = binding
                        if binding in imported:
                            target, name = imported[binding]
                    elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                        binding = call.func.value.id
                        imported_module = imported.get(binding)
                        if imported_module and imported_module[1] is None:
                            target, _ = imported_module
                            name = call.func.attr
                    if not binding or not name or binding in info['globals']:
                        continue
                    if function and locals_.names[binding]:
                        continue
                    if info['bindings'][binding] != 1:
                        continue
                    if function is None and declared_at.get(binding, (sys.maxsize, 0)) > (call.lineno, call.col_offset):
                        continue
                    if binding in info['modified_bindings']:
                        continue
                    if (target['path'], name) in blocked_exports:
                        continue
                    callee = infos.get(target['path'], {}).get('defs', {}).get(name)
                    if callee:
                        emit('calls', file, target, rule='python-unique-module-function', line=call.lineno,
                             caller=observation['owner_id'], callee=callee['id'], expression=observation['target'],
                             start_byte=observation['start_byte'], end_byte=observation['end_byte'])
        elif file['language'] in {'javascript', 'typescript', 'tsx'}:
            for observation in facts['imports']:
                spec = observation.get('source') or ''
                if not spec.startswith(('./', '../')):
                    continue
                path = posixpath.normpath(posixpath.join(posixpath.dirname(file['path']), spec))
                if path.startswith('../'):
                    continue
                candidates = [path] if posixpath.splitext(path)[1] else [
                    path + suffix for suffix in ('.ts', '.tsx', '.js', '.jsx', '/index.ts', '/index.tsx', '/index.js', '/index.jsx')]
                targets = [by_path[candidate] for candidate in candidates if candidate in by_path]
                if len(targets) == 1:
                    emit('imports', file, targets[0], rule='unique-relative-js-module',
                         line=(observation.get('span') or {}).get('start_line', 0) + 1, expression=spec)

    routes = defaultdict(list)
    for file in files:
        native = json.loads(file['facts_json'])['native']
        for route in native.get('route_defs', []):
            path = route.get('path') or ''
            if route.get('framework') == 'file_route' and not any(token in path for token in ('[', ']', '?', '*')):
                routes[(route.get('method'), path)].append(file)
    for file in files:
        native = json.loads(file['facts_json'])['native']
        for call in native.get('http_calls', []):
            if call.get('method') == 'ANY':
                continue
            targets = routes.get((call.get('method'), call.get('path')), [])
            if len(targets) == 1:
                emit('http_routes', file, targets[0], rule='unique-file-route-method-path',
                     expression=f"{call['method']} {call['path']}")
    if len(links) > 50000:
        raise ValueError('Snapshot exceeds 50000 static relationship candidates')
    return [links[key] for key in sorted(links)]


async def read_relationships(driver, project_id: str, *, kind: str = 'calls', file_path: str = '',
                             direction: str = 'out', limit: int = 50, after: str = '', symbol_id: str = '') -> dict | None:
    if kind not in {'calls', 'imports', 'http_routes'} or direction not in {'out', 'in'} or not 1 <= limit <= 100:
        raise ValueError('Use calls/imports/http_routes, out/in and limit 1..100')
    if after and (len(after) != 64 or any(char not in '0123456789abcdef' for char in after)):
        raise ValueError('Relationship cursor must be a lowercase SHA256')

    if len(symbol_id) > 512:
        raise ValueError('Symbol ID exceeds 512 characters')
    field = 'caller_id' if direction == 'out' else 'callee_id'
    needle = canonical(field) + ':' + canonical(symbol_id)

    async def read(tx):
        publications = await (await tx.run('MATCH (p:OutlinePublication {id:$project}) '
                                          'RETURN p.run_id AS run_id, p.manifest_json AS manifest',
                                          project=project_id)).data()
        if not publications:
            return None
        publication = publications[0]
        endpoint = 'a' if direction == 'out' else 'b'
        rows = await (await tx.run(
            'MATCH (a:File)-[r:EVIDENCE_LINK]->(b:File) '
            'WHERE a.project_id=$project AND b.project_id=$project AND r.project_id=$project '
            'AND r.run_id=$run AND r.kind=$kind AND r.id > $after '
            f'AND ($file = "" OR {endpoint}.path=$file) '
            'AND ($symbol = "" OR r.payload_json CONTAINS $needle) '
            'RETURN r.id AS id, r.payload_json AS payload, a.path AS source, b.path AS target '
            'ORDER BY id LIMIT $count', project=project_id, run=publication['run_id'], kind=kind,
            file=file_path, after=after, count=limit + 1, symbol=symbol_id, needle=needle,
        )).data()
        return publication, rows

    async with driver.session() as session:
        result = await session.execute_read(read)
    if result is None:
        return None
    publication, rows = result
    manifest = json.loads(publication['manifest'])
    contract = manifest.get('relationships')
    if not contract or contract.get('version') != 1:
        return {'project_id': project_id, 'run_id': publication['run_id'], 'status': 'reindex-required-for-relationships-v1'}
    hashes = {file['path']: file['sha256'] for file in manifest['files']}
    identities = set(contract['ids'])
    links = []
    for row in rows:
        payload = json.loads(row['payload'])
        if row['id'] not in identities or hashlib.sha256(row['payload'].encode()).hexdigest() != row['id']:
            raise RuntimeError('Relationship does not match its publication')
        if payload['kind'] != kind or payload['source_file'] != row['source'] or payload['target_file'] != row['target']:
            raise RuntimeError('Relationship endpoints do not match its evidence')
        if (hashes.get(row['source']) != payload['source_sha256']
                or hashes.get(row['target']) != payload['target_sha256']):
            raise RuntimeError('Relationship source citations do not match its publication')
        if symbol_id and payload.get(field) != symbol_id:
            raise RuntimeError('Relationship symbol filter does not match its evidence')
        links.append(dict(payload, id=row['id'], run_id=publication['run_id']))
    output = {'project_id': project_id, 'run_id': publication['run_id'], 'relationships': links[:limit],
              'next_cursor': rows[limit - 1]['id'] if len(rows) > limit else None,
              'semantics': 'static-source-candidates'}
    if len(canonical(output).encode()) > 48000:
        raise ValueError('Relationship output exceeds byte budget; reduce limit')
    return output
