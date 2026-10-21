"""Bounded optional PDF text extraction; outputs derived text, never executes PDF actions."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys


def extract(path: Path) -> dict:
    import pypdf

    raw = path.read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('PDF exceeds the 32 MiB input budget')
    reader = pypdf.PdfReader(BytesIO(raw))
    if reader.is_encrypted or len(reader.pages) > 1000:
        raise ValueError('Encrypted or over-budget PDF is not supported')
    pages = []
    text_bytes = 0
    for index, page in enumerate(reader.pages, 1):
        references = []
        annotations = page.get('/Annots', [])
        if hasattr(annotations, 'get_object'):
            annotations = annotations.get_object()
        for reference in annotations:
            annotation = reference.get_object()
            action = annotation.get('/A')
            if hasattr(action, 'get_object'):
                action = action.get_object()
            if action and action.get('/URI'):
                references.append(str(action['/URI']))
        text = page.extract_text() or ''
        text_bytes += len(text.encode('utf-8'))
        if text_bytes > 16 * 1024 * 1024:
            raise ValueError('PDF extracted text exceeds the 16 MiB budget')
        pages.append({'page': index, 'text': text, 'annotation_urls': references})
    return {'schema': 'lessons-pdf-extraction/1', 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'extractor': 'pypdf/' + pypdf.__version__, 'pages': pages,
            'blank_pages': [page['page'] for page in pages if not page['text'].strip()],
            'limitations': ['Text is extracted, not a byte-for-byte transcription of PDF layout.',
                            'Image-only words require separate OCR and review.']}


if __name__ == '__main__':
    result = extract(Path(sys.argv[1]))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
