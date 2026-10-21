"""Prepare a bounded, model-authored James investigation for human review.

The function reads an existing structured corpus. It neither acquires sources
nor invokes inference, accepts a lesson, releases evidence, or trains a model.
Model review is an attributed assessment, not independent verification or truth.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from graphrag_core.lessons_corpus.acquire import _write_private
from graphrag_core.lessons_corpus.corpus import Corpus, sha256
from graphrag_core.lessons_corpus.exchange import _generation, make_review_packet, write_artifact
from graphrag_core.lessons_corpus.ledger import LessonLedger


SOURCE_IDS = ('scripture:engwebp:JAS', 'scripture:eng-asv:JAS')
MODEL_IDENTITY = {'kind': 'model', 'name': 'Codex pilot interpretation',
                  'model_id': 'exact-session-model-identity-unavailable'}
QUESTION = (
    'In James 2:14–26, are actions presented only as evidence of professed faith, '
    'or also as completing it—and what does the passage establish about responding '
    'to someone lacking food or clothing?'
)
LESSON_ID = 'james-practical-help'
_CONTEXT = (
    'One-book pilot; James 2:14–26 in the selected WEB and ASV editions. '
    'These are model-authored textual interpretations, not attributed scholarly '
    'positions, human review, or observed events. Cross-reference suggestions '
    'remain dataset assertions, not evidence that an interpretation is true.'
)
_LIMITS = (
    'The two English editions do not independently settle theology, justification, '
    'salvation, authorship, manuscript history, or real-world effects. Competing '
    'readings overlap. No human assessment or measured outcome is recorded.'
)


def _fresh_path(path: Path, *, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError(f'{label} requires an absolute canonical path')
    if path.exists() or path.is_symlink():
        raise ValueError(f'{label} must be fresh')
    if not path.parent.is_dir():
        raise ValueError(f'{label} requires an existing private parent directory')
    info = path.parent.stat()
    if (stat.S_IMODE(info.st_mode) != 0o700
            or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
        raise ValueError(f'{label} parent requires current-user ownership and mode 700')
    return path


def _verses(source: dict) -> dict[str, dict]:
    result = {}
    verses = source.get('ast', {}).get('verses')
    if type(verses) is not list or not verses:
        raise ValueError('James investigation requires structured verse observations')
    for verse in verses:
        if type(verse) is not dict:
            raise ValueError('Invalid structured verse observation')
        identifier = verse.get('id')
        span = verse.get('text_span')
        match = re.fullmatch(r'JAS\.([1-5])\.([1-9][0-9]*)', identifier) if type(identifier) is str else None
        if (match is None or identifier in result or type(span) is not list
                or len(span) != 2 or any(type(part) is not int for part in span)):
            raise ValueError('Invalid or duplicate structured verse observation')
        if (type(verse.get('chapter')) is not int or type(verse.get('verse')) is not int
                or (verse['chapter'], verse['verse']) != tuple(map(int, match.groups()))):
            raise ValueError('Structured verse identifier does not match its chapter and verse')
        start, end = span
        if (not 0 <= start < end <= len(source['text'])
                or source['text'][start:end] != verse.get('text')):
            raise ValueError('Structured verse does not match its exact Unicode text span')
        result[identifier] = verse
    return result


def _citation(source: dict, verse: dict) -> dict:
    start, end = verse['text_span']
    return {key: source[key] for key in ('source_id', 'source_sha256', 'text_sha256')} | {
        'start_char': start, 'end_char': end, 'quote': source['text'][start:end],
    }


def _record(kind: str, statement: str, citations: list[dict],
            counter_citations: list[dict], *, uncertainty: str = _LIMITS) -> dict:
    return {'kind': kind, 'statement': statement, 'author': dict(MODEL_IDENTITY),
            'context': _CONTEXT, 'values': ['care', 'interpretive humility'],
            'affected_parties': ['people needing help', 'readers'],
            'uncertainty': uncertainty, 'citations': citations,
            'counter_citations': counter_citations, 'outcomes': []}


def _quoted(text: str) -> str:
    # A literal fence keeps source wording as data even if it contains Markdown.
    fence = '~' * max(3, max((len(part) for part in text.split() if set(part) == {'~'}), default=0) + 1)
    return f'{fence}text\n{text}\n{fence}\n'


def _document(sources: list[dict], verses: list[dict], findings: list[dict], lesson: dict,
              packet_hash: str) -> str:
    lines = ['# James investigation: pending human review', '', QUESTION, '',
             'This is a model-authored investigation. Model identity is not verified: '
             '`exact-session-model-identity-unavailable`. No human review, lesson acceptance, '
             'release, training permission, or measured outcome is recorded.', '',
             '## Findings and competing readings', '']
    for event in findings:
        lines.extend([f"### {event['lesson_id']} — {event['status']} by model review", '',
                      event['record']['statement'], '', event['record']['uncertainty'], '',
                      f"Review reason: {event['review']['reason']}", '',
                      f"Exact revision: {event['revision']} / `{event['revision_sha256']}`", ''])
    lines.extend(['The evidential and completion readings overlap; they are competing '
                  'emphases rather than a claim that all theological alternatives are exhausted. '
                  'The captured wording differences at 2:14 and 2:20 are displayed below. '
                  'No Greek or manuscript explanation is inferred.', '',
                  '## Exact edition passages', ''])
    for number in (14, 15, 16, 17, 18, 20, 22, 24, 26):
        identifier = f'JAS.2.{number}'
        lines.extend([f'### James 2:{number}', ''])
        for source, mapping in zip(sources, verses):
            verse = mapping[identifier]
            metadata = source.get('metadata', {})
            lines.extend([f"{metadata.get('edition_title', source['source_id'])} "
                          f"(`{source['source_id']}`, Unicode span {verse['text_span']}):", '',
                          _quoted(verse['text'])])
    lines.extend(['## Tentative lesson — proposed', '', lesson['record']['statement'], '',
                  lesson['record']['uncertainty'], '',
                  'The conditions about wanted help, feasibility and checking results are '
                  'present-day application proposals. They are not quotations from James.', '',
                  'The lesson pins the exact reviewed revisions above. Later finding changes '
                  'require a revised proposal and renewed review before acceptance or release.', '',
                  f"Lesson revision: {lesson['revision']} / `{lesson['revision_sha256']}`", '',
                  f'Review packet SHA-256: `{packet_hash}`', '',
                  '## Attribution and limits', '', _LIMITS, ''])
    for source in sources:
        metadata = source.get('metadata', {})
        lines.extend([f"- Edition: {metadata.get('edition_title', source['source_id'])}",
                      f"  Source: {metadata.get('source_url', 'See captured source metadata')}",
                      f"  Rights: {metadata.get('rights_basis', 'See captured source metadata')}",
                      f"  Rights notice: {metadata.get('rights_url', 'See captured source metadata')}",
                      f"  Source SHA-256: `{source['source_sha256']}`; "
                      f"text SHA-256: `{source['text_sha256']}`", ''])
    return '\n'.join(lines)


def prepare_investigation(corpus: Corpus, ledger_path: Path, output_dir: Path) -> dict:
    """Create fresh private model findings, a pending lesson, and its review packet."""
    ledger_path = _fresh_path(ledger_path, label='Investigation ledger')
    output_dir = _fresh_path(output_dir, label='Investigation output directory')
    if ledger_path == output_dir:
        raise ValueError('Ledger and output directory must be distinct')
    if any(Path(str(ledger_path) + suffix).exists() or Path(str(ledger_path) + suffix).is_symlink()
           for suffix in ('-journal', '-wal', '-shm')):
        raise ValueError('Investigation ledger sidecar paths must be fresh')
    _generation(corpus)
    sources = [corpus.source(identifier) for identifier in SOURCE_IDS]
    if any(source.get('source_id') != identifier for source, identifier in zip(sources, SOURCE_IDS)):
        raise ValueError('Selected James edition did not resolve to its frozen source identifier')
    verses = [_verses(source) for source in sources]
    required = {f'JAS.2.{number}' for number in range(14, 27)}
    if any(not required.issubset(mapping) for mapping in verses):
        raise ValueError('James investigation requires every verse in James 2:14–26')

    def citations(*numbers):
        return [_citation(source, mapping[f'JAS.2.{number}'])
                for number in numbers for source, mapping in zip(sources, verses)]

    definitions = [
        ('james-needs-example', 'accepted', _record(
            'source_claim', 'James 2:15–17 criticizes reassurance unaccompanied by '
            'supplying the bodily necessities described.', citations(15, 16, 17), []),
         'Accepted only as a model assessment of this narrow textual description; '
         'not human review, a historical event, or an empirical finding.'),
        ('james-evidential-reading', 'disputed', _record(
            'hypothesis', 'On an evidential reading, actions reveal whether a profession '
            'of faith has substance; the emphasis is on demonstrating faith.',
            citations(14, 18, 19), citations(22, 24)),
         'Plausible model-authored reading, challenged by the language of completion '
         'and justification. The text does not establish that demonstration is the only role.'),
        ('james-completion-reading', 'disputed', _record(
            'hypothesis', 'On a completion reading, actions participate in bringing faith '
            'to completion; their role exceeds displaying an already complete faith.',
            citations(21, 22, 24, 26), citations(18, 19)),
         'Plausible model-authored reading, held alongside the demonstration language. '
         'No theological reconciliation or independent expert assessment is supplied.'),
        ('james-edition-wording', 'accepted', _record(
            'source_claim', 'The captured WEB and ASV wording at James 2:14 and 2:20 '
            'must remain separately attributable when comparing interpretations.',
            citations(14, 20), [], uncertainty='An edition comparison is not a Greek-language '
            'or manuscript investigation. ' + _LIMITS),
         'Accepted as a model assessment of the need to preserve both exact wordings; '
         'no explanation of translation or textual history is asserted.'),
    ]
    output_dir.mkdir(mode=0o700)
    ledger = LessonLedger(ledger_path, corpus.source)
    findings = []
    for identifier, decision, record, reason in definitions:
        saved = ledger.save(identifier, record, 0)
        findings.append(ledger.review(identifier, saved['revision'], decision,
                                      dict(MODEL_IDENTITY), reason))
    lesson_record = _record(
        'proposed_lesson', 'When someone lacks basic necessities, compassionate words '
        'should be accompanied by appropriate practical help where possible. Ask what '
        'help is wanted and assess whether the response meets the need.',
        citations(15, 16, 17), citations(22, 24),
        uncertainty='This is a tentative present-day application, not a complete account '
        'of James. Consent, feasibility and checking results are additional application '
        'proposals, not words found in the cited passage. ' + _LIMITS)
    # Both theological readings contextualize the narrow application. Neither is
    # represented as contradicting practical help merely to exercise a role.
    roles = ('supports', 'context', 'context', 'context')
    lesson_record['investigation_refs'] = [
        {'record_id': event['lesson_id'], 'revision': event['revision'],
         'revision_sha256': event['revision_sha256'], 'role': role}
        for event, role in zip(findings, roles)
    ]
    lesson = ledger.save(LESSON_ID, lesson_record, 0)
    packet = make_review_packet(corpus, ledger_path, [
        {key: lesson[key] for key in ('lesson_id', 'revision', 'revision_sha256')}
    ])
    packet_path = output_dir / 'review-packet.json'
    packet_hash = write_artifact(packet_path, packet)
    document_path = output_dir / 'investigation-review.md'
    document = _document(sources, verses, findings, lesson, packet_hash).encode('utf-8')
    _write_private(document_path, document)
    summary = {'schema': 'james-investigation/1', 'question': QUESTION,
               'ledger_path': str(ledger_path), 'review_packet_path': str(packet_path),
               'review_document_path': str(document_path),
               'summary_path': str(output_dir / 'summary.json'),
               'review_packet_sha256': packet_hash, 'review_document_sha256': sha256(document),
               'corpus_manifest_sha256': packet['corpus_manifest_sha256'],
               'finding_ids': [event['lesson_id'] for event in findings], 'finding_count': 4,
               'lesson_id': LESSON_ID, 'lesson_status': 'proposed',
               'human_review': 'not_recorded', 'model_identity': dict(MODEL_IDENTITY),
               'source_ids': list(SOURCE_IDS), 'training_allowed': False,
               'records': [{key: event[key] for key in
                            ('lesson_id', 'revision', 'revision_sha256', 'status')}
                           for event in [*findings, lesson]]}
    write_artifact(output_dir / 'summary.json', summary)
    return summary
