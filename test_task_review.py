"""Offline trusted-local agent review acceptance; no inference or services required.

Run with the existing project Python: .venv/bin/python test_task_review.py.
All task state and source fixtures live in disposable private directories.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from memory.task_dispatch import TaskDispatcher
from memory.task_registry import TaskRegistry
from memory.task_review import TaskReviewer


class TaskReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.workspace = self.root / 'repository'
        self.workspace.mkdir(mode=0o700)
        self.source = self.workspace / 'fixture.py'
        self.source.write_text('def total(values):\n    return sum(values)\n', encoding='utf-8')
        self.other = self.workspace / 'notes.py'
        self.other.write_text('SUPPORTED = "integer inputs"\n', encoding='utf-8')
        self.state = self.root / 'state'
        self.registry = TaskRegistry(str(self.state))
        self.dispatcher = TaskDispatcher(self.registry)
        self.reviewer = TaskReviewer(self.registry)
        self.task = self.make_submission()

    def make_submission(self, *, human_review_required=False, raw_transform=None):
        task = self.registry.create('fixture', str(self.workspace),
            'Explain total and its supported inputs', [], ['read_source'],
            human_review_required=human_review_required)
        claimed = self.registry.claim('fixture', task['id'], task['revision'], 'worker-agent')
        citations = []
        retained = []
        for path, end_line in [('fixture.py', 2), ('notes.py', 1)]:
            evidence = self.dispatcher.read_source('fixture', task['id'], claimed['revision'],
                claimed['claim']['token'], path, 1, end_line)
            citations.append({key: evidence[key] for key in ('path', 'start_line', 'end_line', 'sha256')})
            retained.append(evidence)
        finding = dict(schema_version=1, answer='total delegates to sum.',
                       limits=['The fixture documents integer inputs.'], citations=citations)
        if raw_transform is not None:
            finding.update(retained_evidence=retained,
                           validation='source identity/range only; semantic review pending')
            raw_transform(finding)
            return self.registry.submit('fixture', task['id'], claimed['revision'],
                claimed['claim']['token'], finding)
        return self.dispatcher.submit_finding('fixture', task['id'], claimed['revision'],
            claimed['claim']['token'], finding)

    def prepare(self, task=None, **changes):
        task = task or self.task
        arguments = dict(project='fixture', task_id=task['id'], revision=task['revision'], submission=1)
        arguments.update(changes)
        return self.reviewer.prepare_review(**arguments)

    @staticmethod
    def judgments():
        return [dict(target='answer', verdict='supported', reason='The function calls sum.', citations=[1]),
                dict(target='limit:1', verdict='supported', reason='The input note says integer inputs.',
                     citations=[2])]

    def review(self, task=None, **changes):
        task = task or self.task
        context = self.prepare(task)
        arguments = dict(project='fixture', task_id=task['id'], revision=task['revision'], submission=1,
                         reviewer='review-agent', finding_sha256=context['finding_sha256'],
                         decision='accept', reason='Checked every finding target against current source.',
                         judgments=self.judgments())
        arguments.update(changes)
        return self.reviewer.review_agent(**arguments)

    def saved(self, task=None):
        task = task or self.task
        return TaskRegistry(str(self.state)).get('fixture', task['id'])

    def assert_rejected_unchanged(self, operation, task=None):
        task = task or self.task
        before = self.saved(task)
        with self.assertRaises((ValueError, OSError, KeyError, TypeError)):
            operation()
        self.assertEqual(self.saved(task), before)

    def cli(self, operation, **arguments):
        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run([sys.executable, '-m', 'scripts.task_local', '--state', str(self.state)],
            input=json.dumps(dict(operation=operation, arguments=arguments)), capture_output=True,
            text=True, timeout=10, env=env)
        return result, json.loads(result.stdout)

    def test_prepare_rereads_both_sources_without_mutating_task(self):
        context = self.prepare()
        self.assertEqual(len(context['finding_sha256']), 64)
        self.assertTrue(all(char in '0123456789abcdef' for char in context['finding_sha256']))
        self.assertEqual(context['finding'], self.task['submissions'][0]['finding'])
        self.assertEqual(len(context['current_evidence']), 2)
        self.assertEqual(context['current_evidence'][0]['source'], '1: def total(values):\n2:     return sum(values)')
        self.assertEqual(context['current_evidence'][1]['source'], '1: SUPPORTED = "integer inputs"')
        self.assertNotIn('claim_token', json.dumps(context))
        self.assertEqual(self.saved(), self.task)

    def test_supported_review_completes_as_agent_and_reopens(self):
        result = self.review()
        saved = self.saved()
        self.assertEqual(saved, result)
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['reviews'][-1]['actor_type'], 'agent')
        self.assertEqual(saved['reviews'][-1]['submission'], 1)
        self.assertEqual(saved['submissions'], self.task['submissions'])
        self.assertFalse(saved['human_review_required'])

    def test_unsupported_target_requests_correction_and_retains_original(self):
        judgments = self.judgments()
        judgments[0].update(verdict='unsupported', reason='The answer needs an explicit empty-input case.')
        result = self.review(decision='request_correction', judgments=judgments)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result['reviews'][-1]['actor_type'], 'agent')
        self.assertEqual(self.saved()['submissions'], self.task['submissions'])

    def test_uncertainty_cannot_accept_or_request_correction(self):
        judgments = self.judgments()
        judgments[0].update(verdict='unresolved', reason='The behavior needs additional evidence.')
        for decision in ['accept', 'request_correction']:
            with self.subTest(decision=decision):
                self.assert_rejected_unchanged(lambda: self.review(decision=decision, judgments=judgments))
        self.assert_rejected_unchanged(lambda: self.review(decision='request_correction'))
        unsupported = self.judgments()
        unsupported[0]['verdict'] = 'unsupported'
        self.assert_rejected_unchanged(lambda: self.review(judgments=unsupported))

    def test_escalation_persists_human_requirement_and_only_human_can_finish(self):
        result = self.review(decision='escalate', judgments=None,
                             reason='Need a human decision about the scope of the answer.')
        self.assertEqual(result['status'], 'review_pending')
        self.assertTrue(result['human_review_required'])
        self.assertEqual(self.saved(), result)
        self.assert_rejected_unchanged(lambda: self.review(result))
        self.assert_rejected_unchanged(lambda: self.registry.review('fixture', result['id'],
            result['revision'], 'operator', 'accept', 'Checked sources.'))
        accepted = self.registry.review('fixture', result['id'], result['revision'],
            'operator', 'accept', 'Checked sources.', reviewer_kind='human')
        self.assertEqual(accepted['status'], 'completed')
        self.assertEqual(self.saved()['reviews'][-1]['actor_type'], 'human')

    def test_creation_policy_and_legacy_review_actor_are_explicit(self):
        self.assertFalse(self.task['human_review_required'])
        human_task = self.make_submission(human_review_required=True)
        self.assertTrue(human_task['human_review_required'])
        self.assert_rejected_unchanged(lambda: self.review(human_task), human_task)
        for bad_policy in [1, 'true', None, [], {}]:
            with self.subTest(policy=bad_policy), self.assertRaises(ValueError):
                self.registry.create('fixture', str(self.workspace), 'Read', [], ['read_source'],
                                     human_review_required=bad_policy)
        legacy = self.registry.review('fixture', self.task['id'], self.task['revision'],
                                      'legacy-local-reviewer', 'accept', 'Checked sources.')
        self.assertEqual(legacy['status'], 'completed')
        self.assertEqual(legacy['reviews'][-1]['actor_type'], 'unspecified')

    def test_generic_agent_label_cannot_bypass_source_checking_operation(self):
        self.assert_rejected_unchanged(lambda: self.registry.review('fixture', self.task['id'],
            self.task['revision'], 'review-agent', 'accept', 'Unchecked label is insufficient.',
            reviewer_kind='agent'))

    def test_prepared_finding_can_escalate_after_source_disappears(self):
        context = self.prepare()
        self.source.unlink()
        result = self.reviewer.review_agent('fixture', self.task['id'], self.task['revision'],
            1, 'review-agent', context['finding_sha256'], 'escalate',
            'The cited file disappeared after preparation.')
        self.assertEqual(result['status'], 'review_pending')
        self.assertTrue(result['human_review_required'])
        self.assertEqual(self.saved(), result)
        self.assertEqual(result['submissions'], self.task['submissions'])
        self.assertNotIn('source_checks', result['reviews'][-1]['basis'])

    def test_failed_prepare_can_escalate_through_cli_without_claiming_source_checks(self):
        self.source.unlink()
        self.assert_rejected_unchanged(lambda: self.prepare())
        result, payload = self.cli('escalate_review', project='fixture', task_id=self.task['id'],
            revision=self.task['revision'], reviewer='review-agent',
            reason='Source is unavailable; a human must decide the next step.')
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.saved()
        self.assertEqual(payload['result'], saved)
        self.assertEqual(saved['status'], 'review_pending')
        self.assertTrue(saved['human_review_required'])
        self.assertEqual(saved['reviews'][-1]['actor_type'], 'agent')
        self.assertNotIn('source_checks', saved['reviews'][-1]['basis'])

    def test_no_limit_finding_requires_only_answer_and_all_source_citations(self):
        task = self.make_submission(raw_transform=lambda finding: finding.update(limits=[]))
        judgments = [dict(target='answer', verdict='supported',
                          reason='Checked the implementation and the input note.', citations=[1, 2])]
        result = self.review(task, judgments=judgments)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.saved(task), result)

    def test_oversized_review_context_fails_without_discarding_stored_finding(self):
        self.source.write_text('def total(values):\n    return ' + 'x' * 15900 + '\n', encoding='utf-8')
        task = self.make_submission()
        self.assert_rejected_unchanged(lambda: self.prepare(task), task)
        self.assertEqual(self.saved(task)['status'], 'review_pending')

    def test_every_answer_and_limit_requires_one_exact_judgment(self):
        original = self.judgments()
        invalid = [original[:1], original[1:], original + [copy.deepcopy(original[0])],
                   [dict(original[0], target='answer:1'), original[1]],
                   [original[0], dict(original[1], target='limit:0')],
                   [original[0], dict(original[1], target='limit:2')], [], None]
        for judgments in invalid:
            with self.subTest(judgments=judgments):
                self.assert_rejected_unchanged(lambda: self.review(judgments=judgments))

    def test_judgments_require_bounded_reasons_and_exact_citation_indices(self):
        changes = [dict(reason=''), dict(reason=' '), dict(reason='x' * 1025),
                   dict(verdict='likely'), dict(citations=[0]), dict(citations=[True]),
                   dict(citations=[3]), dict(citations=[1, 1]), dict(citations=['1']),
                   dict(citations=[]), dict(citations=None), dict(extra='unrecognized')]
        for change in changes:
            with self.subTest(change=change):
                judgments = self.judgments()
                judgments[0].update(change)
                self.assert_rejected_unchanged(lambda: self.review(judgments=judgments))

    def test_all_supplied_source_citations_must_be_reviewed(self):
        judgments = self.judgments()
        judgments[1]['citations'] = [1]
        self.assert_rejected_unchanged(lambda: self.review(judgments=judgments))

    def test_revision_submission_hash_and_worker_identity_are_fenced(self):
        for changes in [dict(revision=1), dict(revision=True), dict(submission=0),
                        dict(submission=True), dict(submission=2),
                        dict(finding_sha256='0' * 64), dict(reviewer='worker-agent')]:
            with self.subTest(changes=changes):
                self.assert_rejected_unchanged(lambda: self.review(**changes))
        self.assert_rejected_unchanged(lambda: self.prepare(project='other'))
        self.registry.record_assessment('fixture', self.task['id'], self.task['revision'],
                                         1, 'adviser', 'Detached advice changes the revision.')
        self.assert_rejected_unchanged(lambda: self.review())

    def test_changed_or_deleted_source_cannot_be_prepared_or_accepted(self):
        context = self.prepare()
        original = self.source.read_text()
        for contents in ['def total(values):\n    return 0\n', original + '# later edit outside citation\n', None]:
            with self.subTest(contents=contents):
                if contents is None:
                    self.source.unlink()
                else:
                    self.source.write_text(contents, encoding='utf-8')
                self.assert_rejected_unchanged(lambda: self.prepare())
                self.assert_rejected_unchanged(lambda: self.reviewer.review_agent('fixture',
                    self.task['id'], self.task['revision'], 1, 'review-agent', context['finding_sha256'],
                    'accept', 'Checked sources.', self.judgments()))
                self.source.write_text(original, encoding='utf-8')

    def test_file_and_parent_directory_symlinks_are_rejected(self):
        original = self.source.read_bytes()
        outside = self.root / 'same-bytes.py'
        outside.write_bytes(original)
        self.source.unlink()
        self.source.symlink_to(outside)
        self.assert_rejected_unchanged(lambda: self.prepare())
        self.source.unlink()
        self.source.write_bytes(original)
        moved = self.root / 'moved-repository'
        self.workspace.rename(moved)
        self.workspace.symlink_to(moved, target_is_directory=True)
        self.assert_rejected_unchanged(lambda: self.prepare())

    def test_malformed_raw_submission_and_forged_retained_source_are_rejected(self):
        mutations = [lambda finding: finding.pop('retained_evidence'),
                     lambda finding: finding.update(answer='x' * 4097),
                     lambda finding: finding.update(schema_version=True),
                     lambda finding: finding.update(citations=[]),
                     lambda finding: finding['retained_evidence'][0].update(source='1: forged source'),
                     lambda finding: finding['retained_evidence'][0].update(sha256='0' * 64),
                     lambda finding: finding['retained_evidence'][0].update(end_line=1),
                     lambda finding: finding['citations'][0].update(end_line=200),
                     lambda finding: finding['citations'][0].update(path='../same-bytes.py')]
        for mutation in mutations:
            task = self.make_submission(raw_transform=mutation)
            with self.subTest(task_id=task['id']):
                self.assert_rejected_unchanged(lambda: self.prepare(task), task)

    def test_huge_raw_timestamp_rejects_as_value_error_and_controlled_cli_json(self):
        task = self.make_submission(raw_transform=lambda finding:
            finding['retained_evidence'][0].update(observed_at=10**500))
        before = self.saved(task)
        with self.assertRaises(ValueError):
            self.prepare(task)
        result, payload = self.cli('prepare_review', project='fixture', task_id=task['id'],
                                   revision=task['revision'], submission=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload, {'ok': False, 'error': 'ValueError'})
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('OverflowError', result.stderr)
        self.assertEqual(self.saved(task), before)

    def test_malformed_identifiers_reject_without_sqlite_traceback_or_mutation(self):
        context = self.prepare()
        for operation in ['prepare_review', 'review_agent']:
            for changes in [dict(project=[]), dict(task_id={})]:
                with self.subTest(operation=operation, changes=changes):
                    arguments = dict(project='fixture', task_id=self.task['id'],
                                     revision=self.task['revision'], submission=1)
                    if operation == 'review_agent':
                        arguments.update(reviewer='review-agent', decision='accept',
                            finding_sha256=context['finding_sha256'], reason='Checked sources.',
                            judgments=self.judgments())
                    arguments.update(changes)
                    with self.assertRaises(ValueError):
                        getattr(self.reviewer, operation)(**arguments)
                    result, payload = self.cli(operation, **arguments)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(payload, {'ok': False, 'error': 'ValueError'})
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertNotIn('sqlite', result.stderr)
                    self.assertEqual(self.saved(), self.task)

    def test_cli_human_review_policy_rejects_integer_and_string(self):
        for policy in [1, 'true']:
            with self.subTest(policy=policy):
                result, payload = self.cli('create', project='fixture', workspace=str(self.workspace),
                    goal='Read', evidence=[], actions=['read_source'], human_review_required=policy)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(payload, {'ok': False, 'error': 'ValueError'})
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(self.saved(), self.task)

    def test_cli_prepare_agent_acceptance_and_reopen(self):
        identity = dict(project='fixture', task_id=self.task['id'])
        prepared, payload = self.cli('prepare_review', **identity,
                                     revision=self.task['revision'], submission=1)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertTrue(payload['ok'])
        reviewed, payload = self.cli('review_agent', **identity,
            revision=self.task['revision'], submission=1, reviewer='cli-review-agent',
            finding_sha256=payload['result']['finding_sha256'], decision='accept',
            reason='Compared both cited files with each target.', judgments=self.judgments())
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['result']['status'], 'completed')
        reopened, payload = self.cli('get', **identity)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertEqual(payload['result'], self.saved())
        self.assertEqual(payload['result']['reviews'][-1]['actor_type'], 'agent')

    def test_cli_rejects_self_review_without_private_payload_or_mutation(self):
        context = self.prepare()
        private_reason = 'private fixture review reason'
        result, payload = self.cli('review_agent', project='fixture', task_id=self.task['id'],
            revision=self.task['revision'], submission=1, reviewer='worker-agent',
            finding_sha256=context['finding_sha256'], decision='accept', reason=private_reason,
            judgments=self.judgments())
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload['ok'])
        self.assertNotIn(private_reason, result.stdout + result.stderr)
        self.assertNotIn('def total', result.stdout + result.stderr)
        self.assertEqual(self.saved(), self.task)


if __name__ == '__main__':
    unittest.main()
