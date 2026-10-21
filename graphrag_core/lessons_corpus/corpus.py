"""Build and query an immutable, source-mapped local document corpus.

Original response bytes belong to the acquisition layer. This layer preserves
decoded source fields separately from derived text and structural observations.
Neither parsing nor search invokes a model or changes the application's index.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from urllib.parse import quote, urldefrag, urlsplit
import uuid

from graphrag_core.lessons_corpus.ast import parse_html_document, parse_text_document


SCHEMA = 'lessons-corpus/1'
PROSE_URL = re.compile(r'''(?<![\w])https?://[^\s<>"']+''', re.IGNORECASE)
LITERAL_BOUNDARY_POLICY = 'terminal-period-comma-and-unmatched-bracket/1'


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _recovery_context(acquisition: dict) -> dict | None:
    context = acquisition.get('reference_recovery')
    if context is None:
        return None
    fields = {'parent_manifest_sha256', 'target_id', 'original_target_url', 'relation',
              'assessment', 'provenance'}
    if (type(context) is not dict or set(context) != fields
            or any(type(context[key]) is not str for key in fields)
            or not re.fullmatch(r'[0-9a-f]{64}', context['parent_manifest_sha256'])
            or sha256(context['original_target_url'].encode()) != context['target_id']
            or context['relation'] not in {'candidate_counterpart', 'same_url_recheck'}
            or context['assessment'] != 'unreviewed'
            or context['provenance'] != 'operator_selected_candidate'):
        raise ValueError('Invalid reference recovery provenance')
    return dict(context)


def interpretation_status(source: dict) -> str:
    """Keep recovered candidates visibly distinct from adjudicated source identity."""
    status = 'retrieved_source_wording; not verified claim'
    if source.get('metadata', {}).get('reference_recovery'):
        status += '; reference_recovery_unreviewed; historical_equivalence_not_established'
    return status


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Corpus output cannot be a symlink')
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Content-addressed artifact collision')
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def _safe_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {'.', '..'} for part in path.parts):
        raise ValueError('Invalid corpus artifact path')
    result = root
    for part in path.parts:
        result = result / part
        if result.is_symlink():
            raise ValueError('Symlink corpus artifacts are refused')
    if not result.is_file():
        raise ValueError('Missing corpus artifact')
    return result


def _lines(path: Path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _blob(snapshot: Path, acquisition: dict) -> bytes:
    path = _safe_file(snapshot, acquisition['raw_path'])
    raw = path.read_bytes()
    if sha256(raw) != acquisition['body_sha256']:
        raise ValueError('Original acquisition hash mismatch')
    return raw


def _encoding(acquisition: dict) -> str:
    return acquisition.get('encoding') or 'utf-8'


def _pdf_pages(path: Path, executable: str) -> dict:
    worker = Path(__file__).parents[2] / 'scripts' / 'extract_lessons_pdf.py'
    result = subprocess.run([executable, str(worker), str(path)], capture_output=True,
                            timeout=180, check=False)
    if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
        raise ValueError('PDF extraction failed or exceeded its output budget')
    output = json.loads(result.stdout)
    if output.get('schema') != 'lessons-pdf-extraction/1':
        raise ValueError('Unexpected PDF extraction contract')
    return output


def _verified_document(document: dict, snapshot: Path, by_id: dict, payloads: dict) -> None:
    """Bind every normalized field to the exact acquired WordPress JSON object."""
    from graphrag_core.lessons_corpus.acquire import _document

    acquisition_id = document['raw_acquisition_id']
    if by_id[acquisition_id].get('purpose') != 'rest:' + document['kind']:
        raise ValueError('Document kind does not match its acquisition purpose')
    if acquisition_id not in payloads:
        payloads[acquisition_id] = json.loads(_blob(snapshot, by_id[acquisition_id]))
    payload = payloads[acquisition_id]
    if not isinstance(payload, list):
        raise ValueError('WordPress document did not originate from a public object array')
    object_id = document['id'].split(':', 1)[1]
    matches = [obj for obj in payload if isinstance(obj, dict) and str(obj.get('id')) == object_id]
    if len(matches) != 1 or _document(document['kind'], matches[0], acquisition_id) != document:
        raise ValueError('Document fields do not match acquired original JSON')


def _literal_reference(value: str, base_url: str) -> dict:
    """Classify a scalar reference as data using the same parser URL rules."""
    ast = parse_html_document('<a href="' + html.escape(value, quote=True) + '"></a>', base_url)
    reference = ast['references'][0]
    return {key: reference[key] for key in ('resolved_url', 'target_status', 'target_reason')}


def _literal_boundary(raw: str) -> str:
    """Select a conservative URL candidate while retaining the complete match elsewhere."""
    candidate = raw
    pairs = {')': '(', ']': '[', '}': '{'}
    while candidate:
        previous = candidate
        candidate = candidate.rstrip('.,')
        if candidate and candidate[-1] in pairs:
            closing = candidate[-1]
            if candidate.count(closing) > candidate.count(pairs[closing]):
                candidate = candidate[:-1]
        if candidate == previous:
            break
    return candidate


def _prose_url_references(ast: dict, source: str, base_url: str, *, pdf: bool = False) -> list[dict]:
    """Observe literal URL wording, preserving transformation and disjoint-source lineage."""
    result = []
    for match in PROSE_URL.finditer(ast['text']):
        raw = match.group()
        candidate = _literal_boundary(raw)
        first, last = match.start(), match.start() + len(candidate)
        mapped, spans, node_ids = [], [], []
        mapping_complete = True
        entity_expansion = False
        covered_until = first
        for segment in ast['segments']:
            text_span = segment['text_span']
            if text_span is None or text_span[1] <= first or text_span[0] >= last:
                continue
            overlap = [max(first, text_span[0]), min(last, text_span[1])]
            if overlap[0] != covered_until:
                mapping_complete = False
            covered_until = overlap[1]
            source_span = segment['source_span']
            if source_span is None:
                mapping = 'generated-text-without-source-span'
                mapping_complete = False
            elif segment['text'] == segment['raw']:
                source_span = [source_span[0] + overlap[0] - text_span[0],
                               source_span[0] + overlap[1] - text_span[0]]
                mapping = 'copied-text'
            elif segment['kind'] == 'entity':
                entity_expansion = True
                mapping = 'decoded-entity'
                if overlap != text_span:
                    mapping = 'partial-decoded-entity'
                    mapping_complete = False
            else:
                mapping = 'nonlinear-source-transformation'
                mapping_complete = False
            mapped.append({'segment_id': segment['id'], 'node_id': segment['node_id'],
                           'text_span': overlap, 'source_span': source_span, 'mapping': mapping})
            if segment['node_id'] not in node_ids:
                node_ids.append(segment['node_id'])
            if source_span is not None:
                spans.append(source_span)
        if covered_until != last:
            mapping_complete = False
        contiguous = (mapping_complete and bool(spans)
                      and all(left[1] == right[0] for left, right in zip(spans, spans[1:])))
        source_span = [spans[0][0], spans[-1][1]] if contiguous else None
        limitations = ['Literal URL classification is an observation; a URL in prose is not an authenticated hyperlink.']
        if candidate != raw:
            limitations.append('Sentence punctuation uses a declared boundary heuristic; the complete matched literal is retained.')
        if pdf:
            limitations.append('Source offsets address extracted page text, not original PDF bytes.')
        elif entity_expansion:
            limitations.append('HTML entity spelling differs from quoted prose; source segments record its decoding.')
        if not contiguous:
            limitations.append('No single source range is asserted for disjoint or nonlinear source segments.')
        reference = {'id': 'prose:' + str(first), 'node_id': node_ids[0] if len(node_ids) == 1 else None,
                     'node_ids': node_ids, 'attribute': 'prose_url_literal', 'scope': 'prose',
                     'raw_attribute_value': source[source_span[0]:source_span[1]] if source_span else None,
                     'observed_url': candidate, 'raw_literal': raw,
                     'trailing_punctuation': raw[len(candidate):], 'boundary_policy': LITERAL_BOUNDARY_POLICY,
                     'source_span': source_span, 'value_span': source_span,
                     'source_spans': spans, 'source_segments': mapped,
                     'text_span': [first, last], 'quote': ast['text'][first:last],
                     'label': candidate, 'label_spans': spans,
                     'provenance_scope': 'extracted-pdf-page-text' if pdf else 'decoded-html-source',
                     'provenance_limitations': limitations, **_literal_reference(candidate, base_url)}
        result.append(reference)
    return result


def _reference_status(reference: dict, seed_host: str, acquired: set[str]) -> str:
    target = reference.get('resolved_url')
    if reference.get('target_status') != 'http' or not target:
        return 'not_fetchable_or_unresolved'
    target = urldefrag(target)[0]
    parsed = urlsplit(target)
    if any(part in {'wp-admin', 'wp-login.php'} for part in parsed.path.split('/')):
        return 'excluded_admin_or_login'
    if target in acquired:
        return 'acquired_original'
    if (parsed.hostname or '').removeprefix('www.') == seed_host.removeprefix('www.'):
        return 'same_site_unacquired'
    return 'external_unacquired'


def build_corpus(snapshot_dir: str | Path, *, pdf_python: str | None = None) -> dict:
    """Represent an explicit capture atomically, refusing to overwrite a prior build."""
    snapshot = Path(snapshot_dir).absolute()
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError('Require a real capture directory')
    output = snapshot / 'representation'
    if output.exists():
        raise ValueError('This capture already has a representation')
    temporary = snapshot / ('.representation-' + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    sources, references, failures, pdf_reports = [], [], [], []
    acquisitions = list(_lines(_safe_file(snapshot, 'acquisitions.jsonl')))
    by_id = {row['id']: row for row in acquisitions}
    acquired = {urldefrag(row.get('final_url', ''))[0] for row in acquisitions
                if row.get('raw_path') and row.get('status') == 200}
    seed_host = next((urlsplit(row.get('final_url', '')).hostname for row in acquisitions
                      if row.get('final_url')), '') or ''
    inventory = json.loads(_safe_file(snapshot, 'inventory.json').read_text())
    capture_coverage = json.loads(_safe_file(snapshot, 'coverage.json').read_text())
    database = None
    try:
        database = sqlite3.connect(temporary / 'search.sqlite3')
        os.chmod(temporary / 'search.sqlite3', 0o600)
        database.execute('CREATE TABLE sources (source_id TEXT PRIMARY KEY, text_sha256 TEXT NOT NULL, source_kind TEXT NOT NULL)')
        database.execute('CREATE VIRTUAL TABLE text_search USING fts5(source_id UNINDEXED, text)')

        def add(source_id: str, value: str, *, mode: str, url: str, kind: str,
                acquisition_id: str, metadata: dict | None = None):
            acquisition = by_id[acquisition_id]
            metadata = dict(metadata or {})
            context = _recovery_context(acquisition)
            if context is not None:
                metadata['reference_recovery'] = context
            if mode == 'usfm':
                from graphrag_core.lessons_corpus.scripture import parse_usfm_document
                ast = parse_usfm_document(value)
            else:
                ast = parse_html_document(value, url) if mode == 'html' else parse_text_document(value)
            if ''.join(token['raw'] for token in ast['source_tokens']) != value:
                raise ValueError('Parser did not preserve every source character')
            source_hash, text_hash = ast['source_text_sha256'], ast['text_sha256']
            ast_data = canonical(ast)
            ast_hash = sha256(ast_data)
            source_path = f'sources/{source_hash}.txt'
            text_path = f'text/{text_hash}.txt'
            ast_path = f'ast/{ast_hash}.json'
            _write(temporary / source_path, value.encode('utf-8'))
            _write(temporary / text_path, ast['text'].encode('utf-8'))
            _write(temporary / ast_path, ast_data)
            source = {'source_id': source_id, 'source_kind': kind, 'url': url,
                      'source_sha256': source_hash, 'text_sha256': text_hash,
                      'ast_sha256': ast_hash, 'parser_version': ast['parser_version'],
                      'source_path': source_path, 'text_path': text_path, 'ast_path': ast_path,
                      'acquisition_id': acquisition_id, 'capture_sha256': acquisition['body_sha256'],
                      'capture_raw_path': acquisition['raw_path'], 'metadata': metadata,
                      'text_characters': len(ast['text']), 'whitespace_words': len(ast['text'].split()),
                      'node_count': len(ast['nodes']), 'reference_occurrences': len(ast['references'])}
            sources.append(source)
            reference_start = len(references)
            database.execute('INSERT INTO sources VALUES (?,?,?)', (source_id, text_hash, kind))
            database.execute('INSERT INTO text_search VALUES (?,?)', (source_id, ast['text']))
            for reference in ast['references']:
                references.append({**reference, 'source_id': source_id,
                                   'source_sha256': source_hash,
                                   'acquisition_status': _reference_status(reference, seed_host, acquired)})
            if mode == 'html' or kind == 'pdf_extracted_page':
                for reference in _prose_url_references(ast, value, url, pdf=kind == 'pdf_extracted_page'):
                    references.append({**reference, 'source_id': source_id, 'source_sha256': source_hash,
                                       'acquisition_status': _reference_status(reference, seed_host, acquired)})
            if kind == 'wordpress_metadata':
                for match in re.finditer(r'"(?:[^"\\]|\\.)*"', value):
                    scalar = json.loads(match.group())
                    if not scalar.startswith(('https://', 'http://', 'mailto:', 'tel:')):
                        continue
                    reference = {'id': 'json:' + str(match.start()), 'node_id': 'n1',
                                 'attribute': 'json_url_string',
                                 'raw_attribute_value': value[match.start() + 1:match.end() - 1],
                                 'json_literal': match.group(),
                                 'decoded_value': scalar, 'label': '',
                                 'source_span': [match.start(), match.end()],
                                 'value_span': [match.start() + 1, match.end() - 1],
                                 'text_span': [match.start(), match.end()],
                                 **_literal_reference(scalar, url)}
                    references.append({**reference, 'source_id': source_id, 'source_sha256': source_hash,
                                       'acquisition_status': _reference_status(reference, seed_host, acquired)})
            source['reference_occurrences'] = len(references) - reference_start
            return source

        payloads = {}
        for document in _lines(_safe_file(snapshot, 'documents.jsonl')):
            acquisition_id = document['raw_acquisition_id']
            if acquisition_id not in by_id:
                raise ValueError('Document refers to an unknown acquisition')
            _verified_document(document, snapshot, by_id, payloads)
            url = document.get('url') or f'https://{seed_host}/'
            provenance = {key: document.get(key) for key in ('id', 'author', 'published_gmt', 'modified_gmt')}
            provenance['document_id'] = document['id']
            for field in ('title_html', 'content_html', 'excerpt_html'):
                value = document.get(field, '')
                if not isinstance(value, str):
                    raise ValueError('Expected a complete Unicode document field')
                add(document['id'] + ':' + field, value, mode='html', url=url,
                    kind='wordpress_' + document['kind'] + '_' + field,
                    acquisition_id=acquisition_id, metadata={**provenance, 'document_field': field})
            if document['kind'] == 'media':
                for field in ('caption', 'description', 'alt_text'):
                    value = document['metadata'].get(field, '')
                    value = value.get('rendered', '') if isinstance(value, dict) else value
                    if isinstance(value, str) and value:
                        add(document['id'] + ':' + field, value,
                            mode='text' if field == 'alt_text' else 'html', url=url,
                            kind='wordpress_media_' + field, acquisition_id=acquisition_id,
                            metadata={**provenance, 'document_field': 'metadata.' + field})
            # Full remaining API metadata stays inspectable; original response bytes
            # are authoritative. This JSON serialization is explicitly derived.
            add(document['id'] + ':metadata', canonical(document.get('metadata', {})).decode(),
                mode='text', url=url, kind='wordpress_metadata', acquisition_id=acquisition_id,
                metadata={**provenance, 'document_field': 'metadata', 'transformation': 'canonical-json/1'})

        for acquisition in acquisitions:
            if not acquisition.get('raw_path') or acquisition.get('status') != 200:
                continue
            if acquisition.get('purpose') in {'scripture_usfm_archive', 'scripture_crossrefs_archive'}:
                from graphrag_core.lessons_corpus.scripture_pilot import scripture_source
                selected = scripture_source(acquisition, _blob(snapshot, acquisition))
                add(selected['source_id'], selected['value'], mode=selected['mode'],
                    url=acquisition['final_url'], kind=selected['kind'],
                    acquisition_id=acquisition['id'], metadata=selected['metadata'])
                continue
            media = acquisition.get('media_type', '').split(';', 1)[0].lower()
            if media in {'text/html', 'application/xhtml+xml'}:
                raw = _blob(snapshot, acquisition)
                try:
                    text = raw.decode(_encoding(acquisition), errors='strict')
                    add('html:' + acquisition['id'], text, mode='html',
                        url=acquisition['final_url'], kind='public_html', acquisition_id=acquisition['id'],
                        metadata={'decoding': _encoding(acquisition), 'scope': 'page-template-and-content'})
                except (UnicodeError, LookupError, ValueError) as error:
                    failures.append({'acquisition_id': acquisition['id'], 'stage': 'html',
                                     'error_type': type(error).__name__})
            # Same-origin references can serve PDF bytes with a generic MIME type.
            # The explicit acquisition purpose still requires parser validation;
            # silently skipping it would falsely claim a complete representation.
            if media == 'application/pdf' or acquisition.get('purpose') == 'referenced_pdf':
                if not pdf_python:
                    failures.append({'acquisition_id': acquisition['id'], 'stage': 'pdf',
                                     'error_type': 'explicit_pdf_runtime_required'})
                    continue
                try:
                    raw_path = _safe_file(snapshot, acquisition['raw_path'])
                    _blob(snapshot, acquisition)
                    extracted = _pdf_pages(raw_path, pdf_python)
                    if extracted.get('source_sha256') != acquisition['body_sha256']:
                        raise ValueError('PDF extractor source identity mismatch')
                    extraction_path = 'pdf/' + acquisition['id'] + '.json'
                    _write(temporary / extraction_path, canonical(extracted))
                    for page in extracted['pages']:
                        source = add('pdf:' + acquisition['id'] + ':page:' + str(page['page']), page['text'],
                            mode='text', url=acquisition['final_url'], kind='pdf_extracted_page',
                            acquisition_id=acquisition['id'],
                            metadata={'page': page['page'], 'extractor': extracted['extractor'],
                                      'transformation': 'PDF text extraction; not original-byte text offsets',
                                      'extraction_path': extraction_path,
                                      'annotation_urls': page.get('annotation_urls', [])})
                        for index, value in enumerate(page.get('annotation_urls', [])):
                            reference = {'id': 'annotation:' + str(index), 'node_id': None,
                                         'attribute': 'pdf_annotation_uri', 'raw_attribute_value': value,
                                         'source_span': None, 'value_span': None, 'text_span': None, 'label': '',
                                         'page': page['page'], **_literal_reference(value, acquisition['final_url'])}
                            references.append({**reference, 'source_id': source['source_id'],
                                               'source_sha256': source['source_sha256'],
                                               'acquisition_status': _reference_status(reference, seed_host, acquired)})
                            source['reference_occurrences'] += 1
                    pdf_reports.append({'acquisition_id': acquisition['id'], 'pages': len(extracted['pages']),
                                        'blank_pages': extracted['blank_pages'], 'extractor': extracted['extractor']})
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    failures.append({'acquisition_id': acquisition['id'], 'stage': 'pdf',
                                     'error_type': type(error).__name__})
        database.commit()
        database.close()
        database = None
        _write(temporary / 'sources.jsonl', b''.join(canonical(row) for row in sources))
        _write(temporary / 'references.jsonl', b''.join(canonical(row) for row in references))
        coverage = {'schema': SCHEMA, 'representation_complete': not failures,
                    'source_count': len(sources), 'reference_occurrences': len(references),
                    'external_reference_targets': len({r['resolved_url'] for r in references
                                                       if r['acquisition_status'] == 'external_unacquired'}),
                    'syntax_nodes': sum(row['node_count'] for row in sources),
                    'wordpress_content_whitespace_words': sum(row['whitespace_words'] for row in sources
                                                              if row['source_kind'] in {
                                                                  'wordpress_post_content_html',
                                                                  'wordpress_page_content_html'}),
                    'processing_failures': failures, 'pdf_extraction': pdf_reports,
                    'capture_coverage': capture_coverage,
                    'limitations': ['External reference contents are not acquired by listing their URLs.',
                                    'Image/OCR and historical/private/deleted material are not established.',
                                    'Source tokens preserve decoded originals; readable prose has explicit transformations.',
                                    'Syntax and search are not adaptive training or proven understanding.']}
        _write(temporary / 'coverage.json', canonical(coverage))
        files = {str(path.relative_to(temporary)): sha256(path.read_bytes())
                 for path in sorted(temporary.rglob('*')) if path.is_file()}
        manifest = {'schema': SCHEMA, 'capture_inventory': inventory,
                    'capture_metadata_hashes': {name: sha256((snapshot / name).read_bytes()) for name in
                                               ('acquisitions.jsonl', 'documents.jsonl', 'inventory.json', 'coverage.json')},
                    'files': files, 'scope': 'preserved-source-and-structural-observations',
                    'training_performed': False, 'model_calls': 0}
        _write(temporary / 'manifest.json', canonical(manifest))
        os.replace(temporary, output)
        return {'output_dir': str(output), 'coverage': coverage}
    except BaseException:
        if database is not None:
            database.close()
        shutil.rmtree(temporary)
        raise


class Corpus:
    """Read immutable sources and source-bound search; never acquire on a read."""

    def __init__(self, path: str | Path):
        self.root = Path(path).absolute()
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('Require an existing corpus representation')
        self.manifest = json.loads(_safe_file(self.root, 'manifest.json').read_text())
        if self.manifest.get('schema') != SCHEMA:
            raise ValueError('Unsupported corpus schema')
        self._checked('sources.jsonl')
        self.sources = {row['source_id']: row for row in _lines(self.root / 'sources.jsonl')}
        self.acquisitions = {row['id']: row for row in _lines(self.root.parent / 'acquisitions.jsonl')}
        self.documents = {row['id']: row for row in _lines(self.root.parent / 'documents.jsonl')}

    def _checked(self, relative: str) -> bytes:
        path = _safe_file(self.root, relative)
        raw = path.read_bytes()
        if sha256(raw) != self.manifest['files'].get(relative):
            raise ValueError('Corpus artifact hash mismatch')
        return raw

    def source(self, source_id: str) -> dict:
        row = self.sources[source_id]
        source = self._checked(row['source_path'])
        text = self._checked(row['text_path'])
        ast_data = self._checked(row['ast_path'])
        if sha256(source) != row['source_sha256'] or sha256(text) != row['text_sha256']:
            raise ValueError('Source identity mismatch')
        ast = json.loads(ast_data)
        if (ast['source_text_sha256'] != row['source_sha256'] or ast['text_sha256'] != row['text_sha256']
                or ast['parser_version'] != row['parser_version'] or ast['text'] != text.decode('utf-8')
                or ''.join(t['raw'] for t in ast['source_tokens']) != source.decode('utf-8')):
            raise ValueError('AST source lineage mismatch')
        raw = _safe_file(self.root.parent, row['capture_raw_path']).read_bytes()
        if sha256(raw) != row['capture_sha256']:
            raise ValueError('Captured original hash mismatch')
        source_text = source.decode('utf-8')
        metadata = row['metadata']
        context = _recovery_context(self.acquisitions[row['acquisition_id']])
        if metadata.get('reference_recovery') != context:
            raise ValueError('Reference recovery source lineage mismatch')
        if metadata.get('document_id'):
            document = self.documents[metadata['document_id']]
            _verified_document(document, self.root.parent, self.acquisitions, {})
            field = metadata['document_field']
            if field == 'metadata':
                expected = canonical(document['metadata']).decode('utf-8')
            elif field.startswith('metadata.'):
                expected = document['metadata'].get(field.split('.', 1)[1], '')
                if isinstance(expected, dict):
                    expected = expected.get('rendered', '')
            else:
                expected = document[field]
            if source_text != expected:
                raise ValueError('Preserved field differs from captured JSON object')
        elif row['source_kind'] == 'public_html':
            if raw.decode(metadata['decoding'], errors='strict') != source_text:
                raise ValueError('Decoded HTML differs from its acquired bytes')
        elif row['source_kind'] == 'pdf_extracted_page':
            extraction = json.loads(self._checked(metadata['extraction_path']))
            page = next(p for p in extraction['pages'] if p['page'] == metadata['page'])
            if extraction['source_sha256'] != row['capture_sha256'] or page['text'] != source_text:
                raise ValueError('PDF page extraction lineage mismatch')
        elif row['source_kind'] in {'scripture_book', 'scripture_cross_references'}:
            from graphrag_core.lessons_corpus.scripture_pilot import scripture_source
            selected = scripture_source(self.acquisitions[row['acquisition_id']], raw)
            if (selected['value'] != source_text or selected['source_id'] != source_id
                    or selected['kind'] != row['source_kind'] or selected['metadata'] != metadata):
                raise ValueError('Scripture source differs from its acquired archive selection')
            if row['source_kind'] == 'scripture_book':
                from graphrag_core.lessons_corpus.scripture import parse_usfm_document
                if parse_usfm_document(source_text) != ast:
                    raise ValueError('Scripture verse structure differs from the checked original')
        return {**row, 'text': text.decode('utf-8'), 'ast': ast}

    def verify(self) -> dict:
        for name in self.manifest['files']:
            self._checked(name)
        for name, expected in self.manifest['capture_metadata_hashes'].items():
            if sha256(_safe_file(self.root.parent, name).read_bytes()) != expected:
                raise ValueError('Capture metadata changed')
        originals = 0
        for acquisition in _lines(self.root.parent / 'acquisitions.jsonl'):
            if acquisition.get('raw_path'):
                _blob(self.root.parent, acquisition)
                originals += 1
        return {'status': 'verified', 'artifact_files': len(self.manifest['files']),
                'sources': len(self.sources), 'original_responses': originals,
                'meaning': 'capture integrity; not truth or completeness of the world'}

    def search(self, query: str, *, limit: int = 10, source_kind: str | None = None) -> list[dict]:
        if not isinstance(query, str) or len(query) > 1024 or not query.strip():
            raise ValueError('Use a bounded nonempty query')
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Search limit must be 1 through 50')
        terms = re.findall(r'\w+', query, re.UNICODE)
        if not terms:
            return []
        expression = ' AND '.join('"' + term.replace('"', '""') + '"' for term in terms)
        self._checked('search.sqlite3')
        uri = 'file:' + quote(str(self.root / 'search.sqlite3'), safe='/') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as database:
            rows = database.execute('SELECT text_search.source_id,text_search.text FROM text_search '
                                    'JOIN sources ON sources.source_id=text_search.source_id '
                                    'WHERE text_search MATCH ? AND (? IS NULL OR sources.source_kind=?) '
                                    'ORDER BY bm25(text_search),text_search.source_id LIMIT ?',
                                    (expression, source_kind, source_kind, limit)).fetchall()
        results = []
        for source_id, indexed_text in rows:
            source = self.source(source_id)
            if sha256(indexed_text.encode('utf-8')) != source['text_sha256']:
                raise ValueError('Search cache source mismatch')
            if source_kind and source['source_kind'] != source_kind:
                continue
            match = re.search(re.escape(terms[0]), source['text'], re.IGNORECASE)
            start = max(0, (match.start() if match else 0) - 140)
            end = min(len(source['text']), start + 512)
            results.append({'source_id': source_id, 'url': source['url'], 'source_kind': source['source_kind'],
                            'citation': {'source_id': source_id, 'source_sha256': source['source_sha256'],
                                         'text_sha256': source['text_sha256'], 'start_char': start, 'end_char': end,
                                         'quote': source['text'][start:end]},
                            'interpretation_status': interpretation_status(source)})
            if len(results) == limit:
                break
        return results
