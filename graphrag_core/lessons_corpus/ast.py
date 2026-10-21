"""Deterministic source-mapped document syntax, without fetching or interpreting it.

Spans are half-open Unicode character offsets, not byte or rendered-screen offsets.
The HTML tree follows parser tokens; it does not claim browser/HTML5 DOM recovery.
Exact decoded source remains recoverable from the ordered source tokens. Derived
prose keeps copied whitespace and explicitly identifies inserted block separators.
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import re
from urllib.parse import urljoin, urlsplit


PARSER_VERSION = 'lessons-document-ast/1'
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_NODES = 200000
VOID_TAGS = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'})
BLOCK_TAGS = frozenset({'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'details',
                        'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer',
                        'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li',
                        'main', 'nav', 'ol', 'p', 'pre', 'section', 'summary', 'table',
                        'tbody', 'td', 'th', 'thead', 'tr', 'ul'})
NONPROSE_TAGS = frozenset({'script', 'style', 'head', 'template'})
URL_ATTRIBUTES = frozenset({'href', 'src', 'action', 'formaction', 'cite', 'poster',
                            'longdesc', 'data', 'background', 'manifest', 'xlink:href'})
COMPOUND_URL_ATTRIBUTES = frozenset({'srcset', 'imagesrcset', 'ping', 'archive'})
ATTRIBUTE = re.compile(
    r'''(?P<name>[^\s/>=<]+)(?:\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s>]*)))?''')


def _source_identity(source: str) -> tuple[str, int]:
    if not isinstance(source, str):
        raise ValueError('Document source must be Unicode text')
    try:
        encoded = source.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError('Document source must be valid Unicode') from error
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ValueError('Document source exceeds the 16 MiB parser budget')
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _target(raw_value: str | None, base_url: str, *, compound: bool = False) -> dict:
    """Classify observed targets only; even supported URLs are never fetched here."""
    if compound:
        return {'resolved_url': None, 'target_status': 'unsupported_compound',
                'target_reason': 'Compound URL attributes are preserved without guessing candidate boundaries.'}
    if raw_value is None:
        return {'resolved_url': None, 'target_status': 'malformed', 'target_reason': 'Missing attribute value.'}
    value = html.unescape(raw_value)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return {'resolved_url': None, 'target_status': 'unsafe', 'target_reason': 'Target contains control characters.'}
    value = value.strip()
    if not value:
        return {'resolved_url': None, 'target_status': 'malformed', 'target_reason': 'Empty target.'}
    if '\\' in value:
        return {'resolved_url': None, 'target_status': 'unsafe',
                'target_reason': 'Backslash targets have ambiguous browser URL semantics.'}
    if any(character.isspace() for character in value) or re.search(r'%(?![0-9a-fA-F]{2})', value):
        return {'resolved_url': None, 'target_status': 'malformed',
                'target_reason': 'Target has unescaped whitespace or malformed percent encoding.'}
    try:
        resolved = urljoin(base_url, value)
        parsed = urlsplit(resolved)
        if parsed.scheme.lower() in {'http', 'https'}:
            if not parsed.hostname or parsed.username is not None or parsed.password is not None:
                raise ValueError('Missing host or embedded credentials')
            parsed.port  # Validate malformed/non-numeric ports without a connection.
            host_status, host_reason = _host_status(parsed.hostname)
            if host_status is not None:
                return {'resolved_url': resolved, 'target_status': host_status, 'target_reason': host_reason}
            return {'resolved_url': resolved, 'target_status': 'http', 'target_reason': None}
        if parsed.scheme.lower() in {'mailto', 'tel', 'sms'}:
            return {'resolved_url': resolved, 'target_status': 'non_fetchable', 'target_reason': None}
        return {'resolved_url': resolved, 'target_status': 'unsafe',
                'target_reason': 'Target scheme is not an HTTP or contact reference.'}
    except ValueError:
        return {'resolved_url': None, 'target_status': 'malformed', 'target_reason': 'Target URL could not be parsed.'}


def _host_status(host: str) -> tuple[str | None, str | None]:
    """Check syntax and literal local addresses; DNS/origin validation is acquisition work."""
    selected = host.rstrip('.').lower()
    if '%' in selected:
        return 'unsafe', 'Encoded or scoped hosts require a separate network-origin policy.'
    try:
        address = ipaddress.ip_address(selected)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            return 'unsafe', 'Target is a literal nonpublic network address.'
        return None, None
    if (selected == 'localhost' or '.' not in selected
            or selected.endswith(('.localhost', '.local', '.internal', '.lan', '.home'))):
        return 'unsafe', 'Target names a local/private host.'
    if re.fullmatch(r'[0-9.]+', selected):
        return 'unsafe', 'Numeric host shorthand has ambiguous local address semantics.'
    try:
        ascii_host = selected.encode('idna').decode('ascii')
    except UnicodeError:
        return 'malformed', 'Target hostname could not be encoded.'
    labels = ascii_host.split('.')
    if len(ascii_host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                  for label in labels):
        return 'malformed', 'Target hostname has invalid label syntax.'
    return None, None


def _attributes(raw: str, start: int) -> list[dict]:
    opening = re.match(r'<\s*[^\s/>]+', raw)
    if opening is None:
        return []
    result = []
    for match in ATTRIBUTE.finditer(raw, opening.end()):
        name = match['name']
        if name in {'?', '!'}:
            continue
        value_group = next((key for key in ('double', 'single', 'bare') if match[key] is not None), None)
        value = match[value_group] if value_group else None
        result.append({'name': name.lower(), 'raw_name': name, 'raw_value': value,
                       'value': html.unescape(value) if value is not None else None,
                       'source_span': [start + match.start(), start + match.end()],
                       'value_span': ([start + match.start(value_group), start + match.end(value_group)]
                                      if value_group else None)})
    return result


class _DocumentParser(HTMLParser):
    def __init__(self, source: str, base_url: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.base_url = base_url
        self.line_starts = [0] + [match.end() for match in re.finditer('\n', source)]
        self.nodes = [{'id': 'n0', 'parent_id': None, 'children': [], 'kind': 'document',
                       'scope': 'prose', 'source_span': [0, len(source)], 'text_span': [0, 0]}]
        self.stack = [0]
        self.segments = []
        self.references = []
        self.tokens = []
        self.parts = []
        self.text_length = 0
        self.last_text_character = ''

    def _position(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _scope(self) -> str:
        return 'nonprose' if any(self.nodes[index]['scope'] == 'nonprose' for index in self.stack) else 'prose'

    def _node(self, kind: str, start: int, end: int, scope: str, **values) -> int:
        if len(self.nodes) >= MAX_NODES:
            raise ValueError('Document exceeds the structural node budget')
        parent = self.stack[-1]
        index = len(self.nodes)
        node = {'id': f'n{index}', 'parent_id': self.nodes[parent]['id'], 'children': [],
                'kind': kind, 'scope': scope, 'source_span': [start, end],
                'text_span': [self.text_length, self.text_length], **values}
        self.nodes.append(node)
        self.nodes[parent]['children'].append(node['id'])
        return index

    def _token(self, kind: str, start: int, end: int) -> None:
        if start < end:
            self.tokens.append({'kind': kind, 'source_span': [start, end], 'raw': self.source[start:end]})

    def _segment(self, node: int, kind: str, scope: str, span: list[int] | None, text: str,
                 *, reason: str | None = None) -> None:
        segment = {'id': f's{len(self.segments)}', 'node_id': self.nodes[node]['id'],
                   'kind': kind, 'scope': scope, 'source_span': span,
                   'raw': self.source[span[0]:span[1]] if span is not None else None,
                   'text': text, 'text_span': None}
        if scope == 'prose':
            segment['text_span'] = [self.text_length, self.text_length + len(text)]
            self.parts.append(text)
            self.text_length += len(text)
            if text:
                self.last_text_character = text[-1]
        if reason is not None:
            segment['reason'] = reason
        self.segments.append(segment)
        self.nodes[node]['text_span'][1] = self.text_length

    def _separator(self, node: int, reason: str) -> None:
        if self._scope() == 'prose' and self.text_length and self.last_text_character not in '\r\n':
            self._segment(node, 'generated_separator', 'prose', None, '\n', reason=reason)

    def _start(self, tag: str, *, self_closing: bool = False) -> None:
        start = self._position()
        raw = self.get_starttag_text()
        end = start + len(raw)
        self._token('start_tag', start, end)
        if tag in BLOCK_TAGS:
            self._separator(self.stack[-1], f'before-{tag}')
        scope = 'nonprose' if tag in NONPROSE_TAGS or self._scope() == 'nonprose' else 'prose'
        attrs = _attributes(raw, start)
        index = self._node('element', start, end, scope, tag=tag, opening_span=[start, end],
                           closing_span=None, attributes=attrs, closing_reason=None)
        for attribute in attrs:
            name = attribute['name']
            if name not in URL_ATTRIBUTES | COMPOUND_URL_ATTRIBUTES:
                continue
            target = _target(attribute['raw_value'], self.base_url, compound=name in COMPOUND_URL_ATTRIBUTES)
            self.references.append({'id': f'r{len(self.references)}', 'node_id': self.nodes[index]['id'],
                                    'tag': tag, 'attribute': name,
                                    'raw_attribute_value': attribute['raw_value'],
                                    'source_span': attribute['source_span'], 'value_span': attribute['value_span'],
                                    'scope': scope, 'label': '', 'label_spans': [], 'text_span': None, **target})
        if self_closing or tag in VOID_TAGS:
            self.nodes[index]['closing_reason'] = 'self-closing' if self_closing else 'void-element'
            if tag in BLOCK_TAGS:
                self._separator(index, f'after-{tag}')
        else:
            self.stack.append(index)

    def handle_starttag(self, tag, attrs):
        self._start(tag)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, self_closing=True)

    def handle_endtag(self, tag):
        start = self._position()
        terminator = self.source.find('>', start)
        end = terminator + 1 if terminator >= 0 else len(self.source)
        self._token('end_tag', start, end)
        found = next((position for position in range(len(self.stack) - 1, 0, -1)
                      if self.nodes[self.stack[position]].get('tag') == tag), None)
        if found is None:
            self._node('unmatched_end_tag', start, end, 'markup', tag=tag)
            return
        for index in self.stack[found + 1:]:
            self.nodes[index]['source_span'][1] = start
            self.nodes[index]['text_span'][1] = self.text_length
            self.nodes[index]['closing_reason'] = 'ancestor-end-tag'
        index = self.stack[found]
        self.nodes[index]['source_span'][1] = end
        self.nodes[index]['text_span'][1] = self.text_length
        self.nodes[index]['closing_span'] = [start, end]
        self.nodes[index]['closing_reason'] = 'explicit-end-tag'
        self.stack = self.stack[:found]
        if tag in BLOCK_TAGS:
            self._separator(index, f'after-{tag}')

    def _leaf(self, kind: str, start: int, end: int, text: str, scope: str | None = None) -> None:
        selected_scope = scope or self._scope()
        self._token(kind, start, end)
        node = self._node(kind, start, end, selected_scope)
        self._segment(node, kind, selected_scope, [start, end], text)

    def handle_data(self, data):
        start = self._position()
        self._leaf('text', start, start + len(data), data)

    def _entity(self, spelling: str) -> None:
        start = self._position()
        end = start + len(spelling)
        if self.source[end:end + 1] == ';':
            end += 1
        self._leaf('entity', start, end, html.unescape(self.source[start:end]))

    def handle_entityref(self, name):
        self._entity('&' + name)

    def handle_charref(self, name):
        self._entity('&#' + name)

    def handle_comment(self, data):
        start = self._position()
        # HTMLParser also reports bogus/abrupt comments here. Locate the close
        # after the exact callback content, rather than assume every close is -->.
        prefix = 4 if self.source.startswith('<!--', start) else 2
        closing = self.source.find('>', start + prefix + len(data))
        end = closing + 1 if closing >= 0 else len(self.source)
        self._leaf('comment', start, end, self.source[start:end], 'nonprose')

    def _declaration(self, kind: str, data: str, prefix: int = 2) -> None:
        start = self._position()
        closing = self.source.find('>', start + prefix + len(data))
        end = closing + 1 if closing >= 0 else len(self.source)
        self._leaf(kind, start, end, self.source[start:end], 'nonprose')

    def handle_decl(self, decl):
        self._declaration('declaration', decl)

    def unknown_decl(self, data):
        self._declaration('declaration', data, 3)

    def handle_pi(self, data):
        self._declaration('processing_instruction', data)

    def result(self, digest: str, byte_count: int) -> dict:
        for index in self.stack[1:]:
            self.nodes[index]['source_span'][1] = len(self.source)
            self.nodes[index]['text_span'][1] = self.text_length
            self.nodes[index]['closing_reason'] = 'end-of-document'
        self.stack = [0]
        # Preserve parser-unconsumed syntax and malformed tails, rather than claim
        # every original character was extracted into visible prose.
        ordered = sorted(self.tokens, key=lambda token: token['source_span'])
        position = 0
        tokens = []
        for token in ordered:
            start, end = token['source_span']
            if start < position or not 0 <= start <= end <= len(self.source):
                raise ValueError('HTML parser returned overlapping or invalid source spans')
            if start > position:
                self._leaf('unparsed', position, start, self.source[position:start], 'nonprose')
                tokens.append(self.tokens[-1])
            tokens.append(token)
            position = end
        if position < len(self.source):
            self._leaf('unparsed', position, len(self.source), self.source[position:], 'nonprose')
            tokens.append(self.tokens[-1])
        text = ''.join(self.parts)
        self.nodes[0]['text_span'] = [0, len(text)]
        by_id = {node['id']: node for node in self.nodes}
        for reference in self.references:
            node = by_id[reference['node_id']]
            first, last = node['text_span']
            if node['scope'] == 'prose' and first < last:
                reference['label'] = text[first:last]
                reference['text_span'] = [first, last]
                reference['label_spans'] = [segment['source_span'] for segment in self.segments
                                            if segment['text_span'] is not None
                                            and first <= segment['text_span'][0] < last
                                            and segment['source_span'] is not None]
            else:
                label = next((attribute for attribute in node.get('attributes', [])
                              if attribute['name'] in {'alt', 'title', 'aria-label'} and attribute['value']), None)
                if label is not None:
                    reference['label'] = label['value']
                    reference['label_spans'] = [label['value_span']]
        return _result(digest, byte_count, len(self.source), text, self.nodes, self.segments,
                       self.references, tokens, 'html-token-tree')


def _result(digest: str, byte_count: int, char_count: int, text: str, nodes: list,
            segments: list, references: list, tokens: list, mode: str) -> dict:
    return {'schema_version': 1, 'parser_version': PARSER_VERSION, 'mode': mode,
            'representation': 'structural-document-ast', 'findings_status': 'syntax-observations-not-interpretation',
            'span_units': {'source': 'unicode-code-points', 'text': 'unicode-code-points', 'bounds': 'half-open'},
            'source_text_sha256': digest, 'source_length_chars': char_count, 'source_length_utf8_bytes': byte_count,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text,
            'root_id': 'n0', 'nodes': nodes, 'segments': segments, 'references': references, 'source_tokens': tokens,
            'limitations': ['Structural syntax is not learned interpretation or adaptive model training.',
                            'HTML token nesting is not a browser HTML5 DOM or proof of rendered visibility.',
                            'Source spans address decoded Unicode source; acquisition bytes need their own hash and decoding record.',
                            'Nonprose and compound URL attributes remain preserved and explicitly scoped.',
                            'HTTP classification checks syntax and literal hosts only; it never establishes DNS/origin safety or fetchability.']}


def parse_html_document(source: str, base_url: str) -> dict:
    """Parse preserved decoded HTML and URL observations without scripts or network I/O."""
    digest, byte_count = _source_identity(source)
    if not isinstance(base_url, str):
        raise ValueError('Base URL must be text')
    try:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Require an absolute HTTP base URL without credentials')
        if any(ord(character) < 32 or character.isspace() for character in base_url) or '\\' in base_url:
            raise ValueError('Base URL cannot have whitespace, controls or backslashes')
        parsed.port
    except ValueError as error:
        raise ValueError('Require a valid absolute HTTP base URL') from error
    parser = _DocumentParser(source, base_url)
    try:
        parser.feed(source)
        parser.close()
    except (AssertionError, ValueError) as error:
        raise ValueError('Document syntax could not be represented completely') from error
    return parser.result(digest, byte_count)


def parse_text_document(source: str) -> dict:
    """Represent literal plaintext without treating markup, links or commands as code."""
    digest, byte_count = _source_identity(source)
    nodes = [{'id': 'n0', 'parent_id': None, 'children': ['n1'], 'kind': 'document', 'scope': 'prose',
              'source_span': [0, len(source)], 'text_span': [0, len(source)]},
             {'id': 'n1', 'parent_id': 'n0', 'children': [], 'kind': 'text', 'scope': 'prose',
              'source_span': [0, len(source)], 'text_span': [0, len(source)]}]
    segments = [{'id': 's0', 'node_id': 'n1', 'kind': 'text', 'scope': 'prose',
                 'source_span': [0, len(source)], 'raw': source, 'text': source, 'text_span': [0, len(source)]}]
    tokens = [{'kind': 'text', 'source_span': [0, len(source)], 'raw': source}] if source else []
    return _result(digest, byte_count, len(source), source, nodes, segments, [], tokens, 'literal-plaintext')
