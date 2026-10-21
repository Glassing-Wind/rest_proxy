"""Import one acquired James pilot, prepare relationships and a pending investigation.

Run from the repository root. Inputs are three publisher/dataset ZIPs beside an
explicit acquisition receipt; this command performs no network or model calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from graphrag_core.lessons_corpus.corpus import Corpus, _write, build_corpus, canonical
from graphrag_core.lessons_corpus.james_investigation import prepare_investigation
from graphrag_core.lessons_corpus.scripture_pilot import import_james_capture, pilot_relationships


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisitions', type=Path, required=True,
                        help='Explicit source-research-receipt/1 JSON beside the three ZIP originals')
    parser.add_argument('--output', type=Path, required=True, help='Fresh private capture directory outside Git')
    args = parser.parse_args(argv)
    imported = import_james_capture(args.acquisitions, args.output)
    built = build_corpus(args.output)
    corpus = Corpus(built['output_dir'])
    relationships = pilot_relationships(corpus)
    _write(args.output / 'relationships.json', canonical(relationships))
    investigation = prepare_investigation(corpus, args.output / 'lesson-ledger.sqlite3', args.output / 'review')
    result = {'schema': 'james-pilot/1', 'capture_dir': imported['output_dir'],
              'corpus_dir': str(corpus.root), 'verification': corpus.verify(),
              'edition_verse_counts': relationships['edition_verse_counts'],
              'cross_references': {'dataset_rows': relationships['dataset_rows'],
                                   'selected_rows': relationships['selected_rows'],
                                   'target_status_counts': relationships['target_status_counts']},
              'relationships_path': str(args.output / 'relationships.json'),
              'investigation': investigation,
              'other_books_represented': False, 'human_lesson_approval': 'pending',
              'training_performed': False, 'service_configuration_changed': False}
    _write(args.output / 'pilot.json', canonical(result))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': type(error).__name__, 'detail': str(error)}), file=sys.stderr)
        raise SystemExit(1)
