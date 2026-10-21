"""Bounded USFM syntax for a James pilot, with reversible Unicode source maps.

This is a deliberately small adapter, not a general USFM implementation. Chapter
and verse markers are milestones: paragraph markers do not end a verse. The
derived text contains verse wording only; headers, word attributes and notes
remain separately source mapped. No theological relation is inferred here.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import PurePosixPath
import re
import stat
import zipfile

from .ast import MAX_NODES, _source_identity


ADAPTER_VERSION = 'lessons-usfm-james/1'
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256
MAX_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_JAMES_BYTES = 256 * 1024
MAX_COMPRESSION_RATIO = 100
MARKER = re.compile(r'\\(?P<name>\+?[a-z][a-z0-9-]*)(?P<end>\*)?')
ATTRIBUTE = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)="([^"\r\n]*)"')
HEADER_MARKERS = frozenset({'id', 'ide', 'usfm', 'h', 'toc1', 'toc2', 'toc3',
                            'mt', 'mt1', 'mt2', 'mt3', 's', 's1', 's2', 'r'})
PARAGRAPH_MARKERS = frozenset({'p', 'm', 'b', 'q', 'q1', 'q2', 'pi', 'pi1', 'nb'})
STYLE_MARKERS = frozenset({'add', 'nd', 'wj', 'it', 'bd', 'bdit', 'sc', 'sup', 'em', 'qt'})
NOTE_FIELDS = {'f': frozenset({'fr', 'ft', 'fq', 'fqa', 'fk', 'fl', 'fp', 'fv'}),
               'x': frozenset({'xo', 'xt', 'xk', 'xq'})}


def read_james_archive(raw: bytes) -> tuple[str, str]:
    """Read one JAS member from a bounded ZIP; never write archive paths to disk."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ARCHIVE_BYTES:
        raise ValueError('USFM ZIP must be nonempty bytes within the 8 MiB budget')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError('USFM ZIP exceeds the member budget')
            total = 0
            names = set()
            selected = []
            for member in members:
                name = member.filename
                path = PurePosixPath(name)
                normalized = str(path).casefold()
                mode = member.external_attr >> 16
                if (not name or member.orig_filename != name or '\\' in name or '\x00' in name or path.is_absolute()
                        or any(part in {'', '.', '..'} for part in name.rstrip('/').split('/'))
                        or ':' in name or normalized in names
                        or any(ord(character) < 32 for character in name)
                        or stat.S_ISLNK(mode)):
                    raise ValueError('USFM ZIP contains an unsafe or duplicate member path')
                names.add(normalized)
                if member.flag_bits & 1:
                    raise ValueError('Encrypted USFM ZIP members are unsupported')
                if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ValueError('USFM ZIP uses unsupported compression')
                total += member.file_size
                if (member.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_EXPANDED_BYTES
                        or member.file_size > max(1, member.compress_size) * MAX_COMPRESSION_RATIO):
                    raise ValueError('USFM ZIP exceeds expanded-size or compression budgets')
                if member.is_dir():
                    continue
                with archive.open(member) as stream:
                    prefix = stream.read(1024)
                if not re.match(rb'(?:\xef\xbb\xbf)?[ \t\r\n]*\\id[ \t]+JAS(?=[ \t\r\n]|$)', prefix):
                    continue
                if member.file_size > MAX_JAMES_BYTES:
                    raise ValueError('James USFM member exceeds the 256 KiB budget')
                with archive.open(member) as stream:
                    content = stream.read(MAX_JAMES_BYTES + 1)
                if len(content) != member.file_size or len(content) > MAX_JAMES_BYTES:
                    raise ValueError('James USFM member has inconsistent or excessive size')
                selected.append((name, content.decode('utf-8')))
            if len(selected) != 1:
                raise ValueError('USFM ZIP must contain exactly one book beginning with id JAS')
            return selected[0]
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, NotImplementedError, EOFError) as error:
        raise ValueError('USFM ZIP could not be read safely') from error


class _UsfmParser:
    def __init__(self, source: str, book: str):
        self.source = source
        self.book = book
        self.nodes = [{'id': 'n0', 'parent_id': None, 'children': [], 'kind': 'document',
                       'scope': 'prose', 'source_span': [0, len(source)], 'text_span': [0, 0]}]
        self.tokens = []
        self.events = []
        self.chapters = []
        self.verses = []
        self.notes = []
        self.styles = []
        self.chapter = None
        self.verse = None
        self.seen_id = False
        self.paragraphs = []
        self._tokenize()

    def _tokenize(self):
        position = 0
        for match in MARKER.finditer(self.source):
            if match.start() > position:
                self._token('text', position, match.start())
            self._token('marker', match.start(), match.end(), marker=match['name'],
                        closing=bool(match['end']))
            position = match.end()
        if position < len(self.source):
            self._token('text', position, len(self.source))
        if len(self.tokens) > MAX_NODES:
            raise ValueError('USFM exceeds the syntax-token budget')
        if any('\\' in token['raw'] for token in self.tokens if token['kind'] == 'text'):
            raise ValueError('USFM contains an unsupported or malformed backslash marker')

    def _token(self, kind, first, last, **fields):
        self.tokens.append({'kind': kind, 'source_span': [first, last],
                            'raw': self.source[first:last], **fields})

    def _parent(self):
        if self.styles:
            return self.styles[-1]['node_id']
        if self.verse:
            return self.verse['node_id']
        return self.chapter['node_id'] if self.chapter else 'n0'

    def _node(self, kind, span, *, parent=None, scope='prose', **fields):
        if len(self.nodes) >= MAX_NODES:
            raise ValueError('USFM exceeds the structural-node budget')
        parent = parent or self._parent()
        node = {'id': f'n{len(self.nodes)}', 'parent_id': parent, 'children': [],
                'kind': kind, 'scope': scope, 'source_span': list(span), 'text_span': None, **fields}
        self.nodes.append(node)
        self.nodes[int(parent[1:])]['children'].append(node['id'])
        return node

    def _segment(self, node, first, last, *, prose=False, reason=None, kind='text'):
        if first == last:
            return
        self.events.append({'node_id': node, 'source_span': [first, last],
                            'raw': self.source[first:last], 'scope': 'prose' if prose else 'nonprose',
                            'kind': kind, 'reason': reason})

    def _markup(self, token, node=None, reason='USFM marker syntax is excluded from verse text'):
        self._segment(node or self._parent(), *token['source_span'], reason=reason, kind='usfm_markup')

    def _line_marker(self, token):
        first = token['source_span'][0]
        prefix = self.source[self.source.rfind('\n', 0, first) + 1:first]
        if prefix.strip('\ufeff \t\r'):
            raise ValueError(f'USFM {token["marker"]} must begin a line')

    def _text(self, token):
        first, last = token['source_span']
        if not self.verse:
            if token['raw'].strip('\ufeff \t\r\n'):
                raise ValueError('USFM has unclassified wording outside a verse or metadata field')
            self._segment(self._parent(), first, last, reason='Whitespace outside verse text')
        else:
            self._segment(self._parent(), first, last, prose=True)

    def _close_verse(self, position):
        if self.styles:
            raise ValueError('USFM inline style crosses a verse/chapter boundary or lacks a close')
        if self.verse:
            self.verse['source_span'][1] = position
            self.nodes[int(self.verse['node_id'][1:])]['source_span'][1] = position
            self.events.append({'control': 'end_verse', 'verse': self.verse})
            self.verse = None

    def _number(self, token, following):
        if following is None or following['kind'] != 'text':
            raise ValueError(f'USFM {token["marker"]} lacks a numeric identifier')
        match = re.match(r'[ \t]+([1-9][0-9]*)(?:[ \t]+|(?=\r?\n)|$)', following['raw'])
        if match is None:
            raise ValueError('Only positive, unbridged numeric chapter and verse identifiers are supported')
        return int(match[1]), following['source_span'][0] + match.end()

    def _note(self, index):
        opening = self.tokens[index]
        marker = opening['marker']
        if not self.verse or self.styles:
            raise ValueError('USFM notes must occur inside a verse and outside inline styles')
        fields = []
        cursor = index + 1
        if cursor >= len(self.tokens) or self.tokens[cursor]['kind'] != 'text':
            raise ValueError('USFM note lacks its caller')
        caller = self.tokens[cursor]
        if not re.fullmatch(r'[ \t]+(?:\+|-|[a-zA-Z0-9]+)[ \t]*', caller['raw']):
            raise ValueError('USFM note has an unsupported caller or unclassified wording')
        cursor += 1
        while cursor < len(self.tokens):
            token = self.tokens[cursor]
            if token['kind'] != 'marker':
                raise ValueError('USFM note contains unclassified wording')
            if token['closing'] and token['marker'] == marker:
                end = token['source_span'][1]
                if not fields or not any(field['marker'] in {'ft', 'xt'} for field in fields):
                    raise ValueError('USFM note lacks a note-text or cross-reference-text field')
                span = [opening['source_span'][0], end]
                node = self._node('note', span, scope='nonprose', marker=marker)
                note = {'id': f'note{len(self.notes)}', 'node_id': node['id'], 'marker': marker,
                        'verse_id': self.verse['id'], 'source_span': span,
                        'raw': self.source[span[0]:span[1]], 'fields': fields,
                        'text': ' '.join(field['text'] for field in fields), 'text_span': None,
                        'status': 'editorial-note-observation-not-independent-evidence'}
                self.notes.append(note)
                self._segment(node['id'], *span, reason='Editorial note retained separately from verse wording',
                              kind='usfm_note')
                return cursor + 1
            if token['closing'] or token['marker'] not in NOTE_FIELDS[marker]:
                raise ValueError('Unsupported or nested marker within a USFM note')
            if cursor + 1 >= len(self.tokens) or self.tokens[cursor + 1]['kind'] != 'text':
                raise ValueError('USFM note field lacks text')
            wording = self.tokens[cursor + 1]
            fields.append({'marker': token['marker'], 'source_span': list(wording['source_span']),
                           'raw': wording['raw'], 'text': ' '.join(wording['raw'].split()),
                           'transformation': 'collapse-whitespace-and-trim'})
            cursor += 2
        raise ValueError('USFM note lacks a closing marker')

    def _word(self, index):
        opening = self.tokens[index]
        if not self.verse or index + 2 >= len(self.tokens):
            raise ValueError('USFM word markup must be a bounded verse word')
        content, closing = self.tokens[index + 1:index + 3]
        if (content['kind'] != 'text' or closing['kind'] != 'marker'
                or closing['marker'] != opening['marker'] or not closing['closing']):
            raise ValueError('Nested or unterminated USFM word markup is unsupported')
        raw = content['raw']
        if not raw.startswith((' ', '\t')) or raw.count('|') != 1:
            raise ValueError('USFM word markup requires one delimiter and one attribute separator')
        visible, attributes = raw[1:].split('|', 1)
        if not visible or visible != visible.strip() or not attributes:
            raise ValueError('USFM word markup has ambiguous wording or attributes')
        parsed = []
        position = 0
        for match in ATTRIBUTE.finditer(attributes):
            gap = attributes[position:match.start()]
            if (gap.strip() or (parsed and not gap)
                    or any(row['name'] == match[1] for row in parsed)):
                raise ValueError('USFM word attributes are malformed or duplicated')
            attribute_start = content['source_span'][0] + 2 + len(visible)
            parsed.append({'name': match[1], 'value': match[2],
                           'source_span': [attribute_start + match.start(), attribute_start + match.end()],
                           'value_span': [attribute_start + match.start(2), attribute_start + match.end(2)]})
            position = match.end()
        if not parsed or attributes[position:].strip():
            raise ValueError('USFM word attributes are malformed')
        node = self._node('word', [opening['source_span'][0], closing['source_span'][1]],
                          marker=opening['marker'], attributes=parsed)
        first = content['source_span'][0]
        split = first + 1 + len(visible)
        self._markup(opening, node['id'])
        self._segment(node['id'], first, first + 1, reason='Inline marker delimiter')
        self._segment(node['id'], first + 1, split, prose=True)
        self._segment(node['id'], split, content['source_span'][1],
                      reason='Word attributes retained as annotation, not verse wording', kind='word_attributes')
        self._markup(closing, node['id'])
        return index + 3

    def parse(self):
        index = 0
        while index < len(self.tokens):
            token = self.tokens[index]
            if token['kind'] == 'text':
                self._text(token)
                index += 1
                continue
            marker = token['marker']
            following = self.tokens[index + 1] if index + 1 < len(self.tokens) else None
            if marker in {'f', 'x'} and not token['closing']:
                index = self._note(index)
                continue
            if marker in {'w', '+w'} and not token['closing']:
                if marker.startswith('+') and not self.styles:
                    raise ValueError('Nested USFM word marker requires an enclosing inline style')
                index = self._word(index)
                continue
            if marker.lstrip('+') in STYLE_MARKERS:
                if not self.verse:
                    raise ValueError('USFM inline styles require a verse')
                if token['closing']:
                    if not self.styles or self.styles[-1]['marker'] != marker:
                        raise ValueError('USFM inline styles have unmatched or crossing closes')
                    style = self.styles.pop()
                    self._markup(token, style['node_id'])
                    self.nodes[int(style['node_id'][1:])]['source_span'][1] = token['source_span'][1]
                    index += 1
                else:
                    if self.styles and not marker.startswith('+'):
                        raise ValueError('Nested USFM inline styles require explicit plus markers')
                    if marker.startswith('+') and not self.styles:
                        raise ValueError('Plus-prefixed USFM inline styles require an enclosing style')
                    if following is None or following['kind'] != 'text' or not following['raw'].startswith((' ', '\t')):
                        raise ValueError('USFM inline style lacks its whitespace delimiter')
                    node = self._node('inline_style', token['source_span'], marker=marker)
                    self._markup(token, node['id'])
                    self.styles.append({'marker': marker, 'node_id': node['id']})
                    first, last = following['source_span']
                    self._segment(node['id'], first, first + 1, reason='Inline marker delimiter')
                    self._segment(node['id'], first + 1, last, prose=True)
                    index += 2
                continue
            if token['closing']:
                raise ValueError(f'Unmatched or unsupported USFM close: {marker}')
            if marker in HEADER_MARKERS:
                self._line_marker(token)
                if self.styles:
                    raise ValueError('USFM metadata cannot interrupt an inline style')
                if following is None or following['kind'] != 'text':
                    raise ValueError('USFM metadata field lacks text')
                lines = following['raw'].splitlines()
                if any(line.strip() for line in lines[1:]):
                    raise ValueError('USFM metadata continuation lines are unsupported')
                if marker == 'id':
                    if self.seen_id or self.chapter or not re.match(rf'[ \t]+{self.book}(?=[ \t\r\n]|$)', following['raw']):
                        raise ValueError('USFM must have exactly one matching book id before its chapters')
                    self.seen_id = True
                elif not self.seen_id:
                    raise ValueError('USFM book id must precede metadata')
                node = self._node('metadata', [token['source_span'][0], following['source_span'][1]],
                                  marker=marker, scope='nonprose')
                self._markup(token, node['id'])
                self._segment(node['id'], *following['source_span'], reason='Metadata excluded from verse wording')
                index += 2
                continue
            if marker in {'c', 'v'}:
                self._line_marker(token)
                if not self.seen_id:
                    raise ValueError('USFM chapters and verses require a preceding book id')
                number, content_start = self._number(token, following)
                self._close_verse(token['source_span'][0])
                if marker == 'c':
                    if self.chapter:
                        self.chapter['source_span'][1] = token['source_span'][0]
                        self.nodes[int(self.chapter['node_id'][1:])]['source_span'][1] = token['source_span'][0]
                    if self.chapters and number <= self.chapters[-1]['chapter']:
                        raise ValueError('USFM chapters must be strictly increasing without duplicates')
                    if self.source[content_start:following['source_span'][1]].strip():
                        raise ValueError('USFM chapter marker contains unclassified wording')
                    node = self._node('chapter', [token['source_span'][0], len(self.source)], parent='n0', chapter=number)
                    self.chapter = {'id': f'{self.book}.{number}', 'chapter': number, 'node_id': node['id'],
                                    'source_span': node['source_span'], 'marker_span': list(token['source_span'])}
                    self.chapters.append(self.chapter)
                    self._markup(token, node['id'])
                    self._segment(node['id'], *following['source_span'], reason='Chapter number and delimiter')
                else:
                    if not self.chapter:
                        raise ValueError('USFM verse requires a chapter')
                    if self.verses and self.verses[-1]['chapter'] == self.chapter['chapter'] and number <= self.verses[-1]['verse']:
                        raise ValueError('USFM verse identifiers must be strictly increasing within a chapter')
                    node = self._node('verse', [token['source_span'][0], len(self.source)],
                                      parent=self.chapter['node_id'], chapter=self.chapter['chapter'], verse=number)
                    self.verse = {'id': f'{self.book}.{self.chapter["chapter"]}.{number}',
                                  'chapter': self.chapter['chapter'], 'verse': number, 'node_id': node['id'],
                                  'source_span': node['source_span'], 'marker_span': list(token['source_span'])}
                    self.verses.append(self.verse)
                    self.events.append({'control': 'start_verse', 'verse': self.verse})
                    self._markup(token, node['id'])
                    self._segment(node['id'], following['source_span'][0], content_start,
                                  reason='Verse number and delimiter')
                    self._segment(node['id'], content_start, following['source_span'][1], prose=True)
                index += 2
                continue
            if marker in PARAGRAPH_MARKERS:
                self._line_marker(token)
                if not self.seen_id or self.styles:
                    raise ValueError('USFM paragraph marker is misplaced')
                node = self._node('paragraph_milestone', token['source_span'], scope='nonprose', marker=marker,
                                  active_verse=self.verse['id'] if self.verse else None)
                self.paragraphs.append({'marker': marker, 'node_id': node['id'],
                                        'source_span': list(token['source_span']),
                                        'verse_id': self.verse['id'] if self.verse else None})
                self._markup(token, node['id'])
                if self.verse:
                    self.events.append({'control': 'paragraph', 'node_id': node['id']})
                index += 1
                continue
            raise ValueError(f'Unsupported USFM marker: {marker}')
        self._close_verse(len(self.source))
        if not self.seen_id or not self.chapters or not self.verses:
            raise ValueError('USFM requires a matching book id, chapter and verse')
        if self.chapter:
            self.chapter['source_span'][1] = len(self.source)
        return self._render()

    def _render(self):
        segments = []
        parts = []
        length = 0
        pending = []
        current = None

        def append(segment, text):
            nonlocal length
            segment['text'] = text
            segment['text_span'] = [length, length + len(text)] if segment['scope'] == 'prose' else None
            segments.append(segment)
            if segment['scope'] == 'prose':
                parts.append(text)
                length += len(text)

        def whitespace():
            nonlocal length
            if pending:
                text = ' ' if current is not None and length > current['text_span'][0] else ''
                for position, segment in enumerate(pending):
                    append(segment, text if position == 0 else '')
                pending.clear()

        for event in self.events:
            if 'control' in event:
                if event['control'] == 'start_verse':
                    if parts:
                        append({'node_id': 'n0', 'source_span': None, 'raw': None, 'scope': 'prose',
                                'kind': 'generated_separator', 'reason': 'New verse milestone'}, '\n')
                    current = event['verse']
                    current['text_span'] = [length, length]
                elif event['control'] == 'end_verse':
                    for segment in pending:
                        append(segment, '')
                    pending.clear()
                    current['text_span'][1] = length
                    if current['text_span'][0] == length:
                        raise ValueError('USFM verse has no visible wording')
                    current = None
                elif current is not None and not pending and length > current['text_span'][0]:
                    pending.append({'node_id': event['node_id'], 'source_span': None, 'raw': None,
                                    'scope': 'prose', 'kind': 'generated_separator',
                                    'reason': 'Paragraph milestone within a continuing verse'})
                continue
            if event['scope'] != 'prose':
                append(dict(event), '')
                continue
            first = event['source_span'][0]
            for match in re.finditer(r'\s+|\S+', event['raw']):
                segment = {**event, 'source_span': [first + match.start(), first + match.end()],
                           'raw': match[0]}
                if match[0].isspace():
                    segment['kind'] = 'normalized_whitespace'
                    segment['reason'] = 'Collapse whitespace runs; trim verse boundaries'
                    pending.append(segment)
                else:
                    whitespace()
                    append(segment, match[0])
        text = ''.join(parts)
        for index, segment in enumerate(segments):
            segment['id'] = f's{index}'
        for verse in self.verses:
            verse['text'] = text[slice(*verse['text_span'])]
            self.nodes[int(verse['node_id'][1:])]['text_span'] = list(verse['text_span'])
        mapped_spans = {}
        for segment in segments:
            if segment['text_span'] is not None:
                mapped_spans.setdefault(segment['node_id'], []).append(segment['text_span'])
        for node in reversed(self.nodes):
            spans = mapped_spans.get(node['id'], [])
            spans += [self.nodes[int(child[1:])]['text_span'] for child in node['children']
                      if self.nodes[int(child[1:])]['text_span'] is not None]
            if spans:
                node['text_span'] = [min(span[0] for span in spans), max(span[1] for span in spans)]
        self.nodes[0]['text_span'] = [0, len(text)]
        for chapter in self.chapters:
            chapter['text_span'] = self.nodes[int(chapter['node_id'][1:])]['text_span']
            if chapter['text_span'] is None:
                raise ValueError('USFM chapter has no verses')
        references = []
        for note in self.notes:
            if note['marker'] != 'x':
                continue
            for field in note['fields']:
                if field['marker'] == 'xt':
                    references.append({'id': f'r{len(references)}', 'node_id': note['node_id'],
                                       'tag': 'usfm-x', 'attribute': 'xt', 'scope': 'nonprose',
                                       'raw_attribute_value': field['raw'],
                                       'source_span': list(field['source_span']),
                                       'value_span': list(field['source_span']),
                                       'label': field['text'], 'label_spans': [list(field['source_span'])],
                                       'text_span': None, 'resolved_url': None,
                                       'target_status': 'non_fetchable',
                                       'target_reason': 'Literal editorial scripture reference; no target resolution attempted.'})
        return {'schema_version': 1, 'parser_version': ADAPTER_VERSION, 'mode': 'bounded-usfm-milestones',
                'representation': 'structural-document-ast', 'findings_status': 'syntax-observations-not-interpretation',
                'span_units': {'source': 'unicode-code-points', 'text': 'unicode-code-points', 'bounds': 'half-open'},
                'source_text_sha256': hashlib.sha256(self.source.encode()).hexdigest(),
                'source_length_chars': len(self.source), 'source_length_utf8_bytes': len(self.source.encode()),
                'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text, 'root_id': 'n0',
                'nodes': self.nodes, 'segments': segments, 'source_tokens': self.tokens,
                'book_id': self.book, 'chapters': self.chapters, 'verses': self.verses,
                'notes': self.notes, 'paragraph_milestones': self.paragraphs, 'references': references,
                'transformations': ['Exclude metadata, marker delimiters and word attributes from verse wording.',
                                    'Preserve inline style wording and original Unicode punctuation.',
                                    'Retain editorial notes separately, without treating them as independent evidence.',
                                    'Collapse verse whitespace runs to one space and trim verse boundaries.',
                                    'Insert a newline between verse milestones; paragraph milestones continue the active verse.'],
                'limitations': ['This bounded James adapter rejects unsupported or ambiguous USFM syntax.',
                                'Verse identifiers and editorial annotations are source observations, not historical proof.',
                                'No translation equivalence, cross-reference truth or theological interpretation is inferred.',
                                'Unicode source spans address decoded text; preserve acquisition bytes and decoding separately.']}


def parse_usfm_document(source: str, *, book: str = 'JAS') -> dict:
    """Represent supported James USFM exactly, deriving source-mapped verse wording."""
    _source_identity(source)
    if book != 'JAS':
        raise ValueError('The first scripture adapter is explicitly bounded to JAS (James)')
    if len(source.encode()) > MAX_JAMES_BYTES:
        raise ValueError('James USFM source exceeds the 256 KiB budget')
    if any(ord(character) < 32 and character not in '\t\r\n' for character in source):
        raise ValueError('USFM source contains unsupported control characters')
    return _UsfmParser(source, book).parse()
