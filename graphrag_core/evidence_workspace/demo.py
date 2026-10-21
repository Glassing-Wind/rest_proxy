"""Explicit synthetic evidence package; never acquires a real repository or account."""
import base64
import io


def demo_package() -> dict:
    """Return deterministic fictional source examples and uncertain relationships."""
    sources = [
        {'id': 'import-source', 'label': 'Synthetic importer source', 'kind': 'provided-code',
         'media_type': 'text/plain', 'source_revision': 'synthetic-v1',
         'original_reference': 'demo/importer.py',
         'text': 'def import_records(raw):\n    rows = parse(raw)\n    return store.write(rows)\n'},
        {'id': 'transfer-record', 'label': 'Fictional transfer record', 'kind': 'synthetic-record',
         'media_type': 'text/plain', 'observed_at': '2026-10-01T12:00:00Z',
         'text': 'DEMO — fictional records\nRecord: demo-01\nAsset: BTC\nQuantity: 0.00100000\n'
                 'Destination: demo-address-A\nStatus: record claims complete\n'},
        {'id': 'message-record', 'label': 'Fictional conversation excerpt', 'kind': 'synthetic-message',
         'media_type': 'text/plain',
         'text': 'DEMO — fictional conversation\nContact alpha: please send to demo-address-A.\n'
                 'Contact alpha: I control that wallet.\n'},
        {'id': 'missing-confirmation', 'label': 'Delivery confirmation not acquired',
         'kind': 'missing-record', 'media_type': 'text/plain', 'availability': 'unavailable'},
    ]
    entities = [
        {'id': 'importer', 'label': 'Import handler', 'kind': 'code-component'},
        {'id': 'storage', 'label': 'Document store', 'kind': 'code-component'},
        {'id': 'transfer', 'label': 'Fictional transfer', 'kind': 'record'},
        {'id': 'address', 'label': 'demo-address-A', 'kind': 'address-literal'},
        {'id': 'contact', 'label': 'Contact alpha (unverified handle)', 'kind': 'source-handle'},
    ]
    relationships = [
        {'id': 'import-writes', 'source': 'importer', 'target': 'storage', 'predicate': 'references-write',
         'statement': 'The importer source references store.write after parse(raw).',
         'evidence_status': 'observation',
         'explanation': 'This excerpt establishes a source reference; runtime dispatch and test coverage need separate evidence.',
         'citations': [{'source_id': 'import-source', 'start_line': 1, 'end_line': 3,
                        'quote': 'return store.write(rows)'}]},
        {'id': 'record-destination', 'source': 'transfer', 'target': 'address',
         'predicate': 'records-destination',
         'statement': 'The fictional transfer record names demo-address-A as its destination.',
         'evidence_status': 'source_claim',
         'explanation': 'A record claim is separate from verifying a payment, fee, receipt or wallet controller.',
         'citations': [{'source_id': 'transfer-record', 'start_line': 2, 'end_line': 6,
                        'quote': 'Destination: demo-address-A'}]},
        {'id': 'controller-hypothesis', 'source': 'contact', 'target': 'address',
         'predicate': 'hypothesized-control',
         'statement': 'Contact alpha controls the destination wallet.',
         'evidence_status': 'inference',
         'explanation': 'This deliberately unsupported conclusion should be rejected or amended. A claimed identity is not verified control.',
         'citations': [{'source_id': 'message-record', 'start_line': 2, 'end_line': 3}]},
    ]
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pass
    else:
        image = Image.new('RGB', (900, 520), '#f6f2e9')
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=34)
        draw.rounded_rectangle((38, 38, 862, 480), 20, fill='#ffffff', outline='#cad5df', width=3)
        draw.text((70, 72), 'DEMO — SYNTHETIC EXHIBIT', fill='#183548', font=font)
        draw.text((70, 170), 'Example card face value: $100', fill='#183548', font=font)
        draw.text((70, 280), 'Not a payment or merchant receipt', fill='#5f7380', font=font)
        draw.text((70, 365), 'No redemption code or real account', fill='#5f7380', font=font)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        sources.append({'id': 'exhibit-image', 'label': 'Synthetic image region', 'kind': 'synthetic-image',
                        'media_type': 'image/png', 'content_base64': base64.b64encode(buffer.getvalue()).decode()})
        relationships.append({
            'id': 'image-face-value', 'source': 'transfer', 'target': 'address', 'predicate': 'image-claim',
            'statement': 'The synthetic image displays a $100 face-value example.',
            'evidence_status': 'source_claim',
            'explanation': 'The selected image region and transcription do not establish purchase or redemption.',
            'citations': [{'source_id': 'exhibit-image', 'bbox': {'x': 0.05, 'y': 0.23, 'width': 0.90, 'height': 0.24},
                           'quote': '$100'}]})
    return {'schema_version': 1, 'case_id': 'demo-workflow', 'title': 'A source-backed investigation',
            'coverage': {'status': 'partial', 'notes': 'Fictional code, records and images. One listed record is missing; another expected source is unlisted.',
                         'expected_source_count': len(sources) + 1},
            'sources': sources, 'entities': entities, 'relationships': relationships}
