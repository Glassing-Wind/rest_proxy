"""Explicit local lessons-corpus acquisition, source search and interpretation ledger."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from graphrag_core.lessons_corpus.corpus import Corpus, build_corpus


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('--url', required=True)
    capture.add_argument('--output', type=Path, required=True)
    capture.add_argument('--rights-basis', required=True)
    capture.add_argument('--max-urls', type=int, default=1200)
    capture.add_argument('--no-public-html', action='store_true')
    build = sub.add_parser('build')
    build.add_argument('--capture', type=Path, required=True)
    build.add_argument('--pdf-python')
    serve = sub.add_parser('serve', help='Explicit authenticated, read-only loopback HTTP service')
    serve.add_argument('--corpus', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8781)
    serve.add_argument('--token-env', default='LM_PROXY_LESSONS_TOKEN',
                       help='Environment variable containing the local access token; never a token value')
    frontier = sub.add_parser('frontier', help='Inspect checked reference targets without fetching')
    frontier.add_argument('--corpus', type=Path, required=True)
    frontier.add_argument('--scope', choices=['authored', 'all'], default='authored')
    frontier.add_argument('--origin', choices=['all', 'same-site', 'external'], default='all')
    frontier.add_argument('--offset', type=int, default=0)
    frontier.add_argument('--limit', type=int, default=50)
    recover = sub.add_parser('recover', help='Explicit bounded capture of selected reference candidates')
    recover.add_argument('--corpus', type=Path, required=True)
    recover.add_argument('--output', type=Path, required=True)
    recover.add_argument('--scope', choices=['authored', 'all'], default='authored')
    recover.add_argument('--selections', type=Path, required=True,
                         help='JSON list of target_id, candidate_url and relation records')
    recover.add_argument('--permissions', type=Path, required=True,
                         help='JSON object mapping explicit hostnames to private processing-rights bases')
    packet = sub.add_parser('review-packet', help='Prepare exact lessons and reviewed findings for human review')
    release = sub.add_parser('release-bundle', help='Prepare a separately approved, data-only local lesson bundle')
    for command in (packet, release):
        command.add_argument('--corpus', type=Path, required=True)
        command.add_argument('--ledger', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
    packet.add_argument('--selection', type=Path, required=True,
                        help='JSON object with lessons: [{lesson_id, revision, revision_sha256}]')
    release.add_argument('--packet', type=Path, required=True)
    release.add_argument('--release', type=Path, required=True,
                         help='Exact packet acknowledgment, destination, human reviewer, reason, rights and use')
    inspect = sub.add_parser('inspect-bundle', help='Inspect a fingerprint-pinned bundle without installing lessons')
    inspect.add_argument('--bundle', type=Path, required=True)
    inspect.add_argument('--expected-sha256', required=True)
    for name in ('verify', 'search', 'show', 'propose', 'review', 'lessons'):
        help_text = {'propose': 'Save a record; lessons require exact reviewed investigation references',
                     'review': 'Append a review; lesson acceptance requires current investigation references'}
        command = sub.add_parser(name, help=help_text.get(name), description=help_text.get(name))
        command.add_argument('--corpus', type=Path, required=True)
        if name == 'search':
            command.add_argument('--query', required=True)
            command.add_argument('--limit', type=int, default=10)
            command.add_argument('--source-kind')
        if name == 'show':
            command.add_argument('--source-id', required=True)
        if name in {'propose', 'review'}:
            command.add_argument('--lesson-id', required=True)
            command.add_argument('--expected-revision', type=int, required=True)
        if name == 'propose':
            command.add_argument('--record', type=Path, required=True)
        if name == 'review':
            command.add_argument('--decision', choices=['accepted', 'disputed', 'rejected'], required=True)
            reviewer = command.add_mutually_exclusive_group(required=True)
            reviewer.add_argument('--reviewer', help='Legacy operator name; not explicit human review')
            reviewer.add_argument('--reviewer-record', type=Path,
                                  help='Explicit JSON human or model identity; operator supplied, not authenticated')
            command.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    if args.command == 'capture':
        from graphrag_core.lessons_corpus.acquire import capture_wordpress
        args.output = args.output.expanduser().absolute()
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = capture_wordpress(args.url, args.output, rights_basis=args.rights_basis,
                                   max_urls=args.max_urls, include_public_html=not args.no_public_html)
        result = {'output_dir': result['output_dir'], 'inventory_file': 'inventory.json',
                  'document_counts': result['inventory']['document_counts'],
                  'acquisitions': result['inventory']['acquisitions'],
                  'response_bytes': result['inventory']['response_bytes'],
                  'coverage_complete': result['coverage']['complete_within_declared_scope'],
                  'bound_reached': result['coverage']['bound_reached'],
                  'changed_endpoints': result['coverage']['changed_endpoints'],
                  'errors': result['coverage']['errors'],
                  'pending_html': len(result['coverage']['pending_html_urls']),
                  'pending_pdf': len(result['coverage']['pending_pdf_urls'])}
    elif args.command == 'build':
        result = build_corpus(args.capture, pdf_python=args.pdf_python)
        result = {'output_dir': result['output_dir'],
                  'representation_complete': result['coverage']['representation_complete'],
                  'sources': result['coverage']['source_count'],
                  'syntax_nodes': result['coverage']['syntax_nodes'],
                  'reference_occurrences': result['coverage']['reference_occurrences'],
                  'external_reference_targets': result['coverage']['external_reference_targets'],
                  'wordpress_content_whitespace_words': result['coverage']['wordpress_content_whitespace_words'],
                  'pdf_extraction': result['coverage']['pdf_extraction'],
                  'processing_failures': result['coverage']['processing_failures']}
    elif args.command == 'serve':
        if not 1024 <= args.port <= 65535:
            raise ValueError('Use a nonprivileged port from 1024 through 65535')
        from graphrag_core.lessons_corpus.server import create_app
        import uvicorn
        app = create_app(str(args.corpus), os.environ.get(args.token_env, ''),
                         allowed_host=f'127.0.0.1:{args.port}')
        uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, proxy_headers=False)
        return 0
    elif args.command in {'review-packet', 'release-bundle', 'inspect-bundle'}:
        from graphrag_core.lessons_corpus.exchange import (
            inspect_bundle, make_review_packet, read_artifact, release_bundle, write_artifact,
        )
        if args.command == 'inspect-bundle':
            result = inspect_bundle(read_artifact(args.bundle), args.expected_sha256)
        else:
            corpus = Corpus(args.corpus)
            if args.command == 'review-packet':
                selection = read_artifact(args.selection)
                if set(selection) != {'lessons'}:
                    raise ValueError('Selection requires exactly the lessons field')
                artifact = make_review_packet(corpus, args.ledger, selection['lessons'])
            else:
                artifact = release_bundle(corpus, args.ledger, read_artifact(args.packet),
                                          read_artifact(args.release))
            result = {'schema': artifact['schema'], 'output': str(args.output),
                      'artifact_sha256': write_artifact(args.output, artifact),
                      'operational_state_changed': False, 'training_performed': False}
    elif args.command == 'frontier':
        from graphrag_core.lessons_corpus.frontier import reference_frontier
        result = reference_frontier(Corpus(args.corpus), scope=args.scope, origin=args.origin,
                                    offset=args.offset, limit=args.limit)
    elif args.command == 'recover':
        from graphrag_core.lessons_corpus.recovery import capture_references
        for path in (args.selections, args.permissions):
            if path.stat().st_size > 256 * 1024:
                raise ValueError('Recovery input file exceeds its byte budget')
        result = capture_references(Corpus(args.corpus), args.output,
                                    json.loads(args.selections.read_text(encoding='utf-8')),
                                    permissions=json.loads(args.permissions.read_text(encoding='utf-8')),
                                    scope=args.scope)
        result = {'output_dir': result['output_dir'], 'coverage': result['coverage'],
                  'bindings': result['inventory']['bindings']}
    else:
        corpus = Corpus(args.corpus)
        if args.command == 'verify':
            result = corpus.verify()
        elif args.command == 'search':
            result = corpus.search(args.query, limit=args.limit, source_kind=args.source_kind)
        elif args.command == 'show':
            result = corpus.source(args.source_id)
        else:
            from graphrag_core.lessons_corpus.ledger import LessonLedger
            ledger = LessonLedger(corpus.root.parent / 'lesson-ledger.sqlite3', corpus.source)
            if args.command == 'propose':
                result = ledger.save(args.lesson_id, json.loads(args.record.read_text()), args.expected_revision)
            elif args.command == 'review':
                reviewer = args.reviewer
                if args.reviewer_record is not None:
                    from graphrag_core.lessons_corpus.exchange import read_artifact
                    reviewer = read_artifact(args.reviewer_record)
                result = ledger.review(args.lesson_id, expected_revision=args.expected_revision,
                                       decision=args.decision, reviewer=reviewer, reason=args.reason)
            else:
                result = ledger.list_lessons()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': type(error).__name__, 'detail': str(error)}), file=sys.stderr)
        raise SystemExit(1)
