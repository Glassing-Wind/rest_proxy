"""Offline cockpit acceptance: synthetic fixtures and MockTransport only; no environment required."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from graphrag_core import cockpit_eval as cockpit


PLAN = Path(__file__).resolve().parent / cockpit.DEFAULT_PLAN
MODEL = 'synthetic-runtime-id'


def findings_for(case: cockpit.Case) -> dict:
    """Construct valid test input; this is never a model result or live pilot evidence."""
    return {
        'schema_version': 1,
        'facts': [
            {'key': fact.key, 'value': fact.value,
             'citations': [{'line': fact.line, 'quote': case.lines[fact.line - 1]}]}
            for fact in case.facts
        ],
        'unresolved': list(case.unresolved),
    }


def envelope_for(case: cockpit.Case, **changes) -> dict:
    envelope = {
        'model': MODEL,
        'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(findings_for(case), ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150,
                  'completion_tokens_details': {'reasoning_tokens': 12}},
    }
    envelope.update(changes)
    return envelope


def lmstudio_envelope_for(case: cockpit.Case, **changes) -> dict:
    envelope = {
        'model_instance_id': MODEL,
        'output': [{'type': 'message', 'content': json.dumps(findings_for(case), ensure_ascii=False)}],
        'stats': {'input_tokens': 100, 'total_output_tokens': 50, 'reasoning_output_tokens': 0},
    }
    envelope.update(changes)
    return envelope


class CockpitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = cockpit.load_cases(PLAN)
        cls.by_id = {case.case_id: case for case in cls.cases}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def assert_stage(self, stage, operation):
        with self.assertRaises(cockpit.TrialError) as caught:
            operation()
        self.assertEqual(caught.exception.stage, stage)

    def run_mock(self, handler, *, name='trial', cases=None, **kwargs):
        return cockpit.run_trials(
            self.cases[:1] if cases is None else cases,
            base_url='http://127.0.0.1:1234/v1', model=MODEL,
            output=self.root / name, transport=httpx.MockTransport(handler), **kwargs)

    def save_plan(self, data):
        path = self.root / 'plan.json'
        path.write_bytes(cockpit.canonical(data))
        return path

    def test_frozen_originals_load_with_expected_unicode_and_conflict(self):
        self.assertEqual(len(self.cases), 6)
        unicode_case = self.by_id['unicode_quote']
        self.assertIn('Café 🌱', unicode_case.lines[0])
        for case in self.cases:
            with self.subTest(case=case.case_id):
                digest = hashlib.sha256(('\n'.join(case.lines) + '\n').encode()).hexdigest()
                self.assertEqual(case.source_sha256, digest)
                self.assertTrue(cockpit.validate_findings(case, findings_for(case))['accepted'])
        conflict = self.by_id['conflicting_revisions']
        self.assertEqual(conflict.unresolved, ('current_raw_byte_limit',))

    def test_modified_source_fails_even_when_plan_hash_is_recomputed(self):
        plan = cockpit.read_json(PLAN)
        source = plan['fixtures'][0]
        source['source_lines'][0] += ' changed'
        source['source_utf8_sha256'] = hashlib.sha256(
            ('\n'.join(source['source_lines']) + '\n').encode()).hexdigest()
        self.assert_stage('source_fingerprint', lambda: cockpit.load_cases(self.save_plan(plan)))

    def test_stale_declared_hash_fails(self):
        plan = cockpit.read_json(PLAN)
        plan['fixtures'][0]['source_utf8_sha256'] = '0' * 64
        self.assert_stage('source_fingerprint', lambda: cockpit.load_cases(self.save_plan(plan)))

    def test_plan_duplicate_unknown_and_unhashable_ids_are_refused(self):
        original = cockpit.read_json(PLAN)
        duplicate = copy.deepcopy(original)
        duplicate['fixtures'][1] = copy.deepcopy(duplicate['fixtures'][0])
        self.assert_stage('plan_contract', lambda: cockpit.load_cases(self.save_plan(duplicate)))
        for replacement in ('unknown', [], {}):
            with self.subTest(replacement=replacement):
                plan = copy.deepcopy(original)
                plan['fixtures'][0]['id'] = replacement
                self.assert_stage('plan_contract', lambda: cockpit.load_cases(self.save_plan(plan)))

    def test_plan_requires_six_single_line_sources(self):
        original = cockpit.read_json(PLAN)
        missing = copy.deepcopy(original)
        missing['fixtures'].pop()
        multiline = copy.deepcopy(original)
        multiline['fixtures'][0]['source_lines'][0] += '\nextra line'
        for plan in (missing, multiline, {'fixtures': None}, []):
            with self.subTest(plan_type=type(plan).__name__):
                self.assert_stage('plan_contract', lambda: cockpit.load_cases(self.save_plan(plan)))

    def test_json_refuses_duplicate_nested_keys_nonfinite_and_invalid_utf8(self):
        samples = (
            (b'{"a":{"b":1,"b":2}}', 'json_duplicate_key'),
            (b'{"a":NaN}', 'json_nonfinite'),
            (b'{"a":Infinity}', 'json_nonfinite'),
            (b'{"a":1e999}', 'json_nonfinite'),
            (b'{"a":"\\ud800"}', 'json_parse'),
            (b'\xff', 'json_parse'),
            (b'```json\n{}\n```', 'json_parse'),
            (b'{} trailing', 'json_parse'),
        )
        for body, stage in samples:
            with self.subTest(body=body):
                self.assert_stage(stage, lambda: cockpit.parse_json(body))
        self.assert_stage('json_size', lambda: cockpit.parse_json(b' {}', limit=2))
        self.assertEqual(cockpit.parse_json(b'{}', limit=2), {})

    def test_endpoint_only_allows_explicit_numeric_loopback_origins(self):
        accepted = {
            'http://127.0.0.1:1234': 'http://127.0.0.1:1234/v1/chat/completions',
            'https://[::1]:8443/v1/': 'https://[::1]:8443/v1/chat/completions',
        }
        for origin, expected in accepted.items():
            self.assertEqual(cockpit.endpoint(origin), expected)
        for origin in (
            'http://localhost:1234', 'https://example.com:443', 'http://192.168.1.2:1234',
            'http://0.0.0.0:1234', 'http://127.0.0.1', 'http://127.0.0.1:0',
            'http://127.0.0.1:65536', 'http://user:secret@127.0.0.1:1234',
            'http://127.0.0.1:1234/path', 'http://127.0.0.1:1234?token=secret',
            'http://127.0.0.1:1234#fragment', 'http://[::1%lo0]:1234',
            ' http://127.0.0.1:1234', 'http://127.0.0.1:1234\n',
            'http://127.0.0.1:1234\\example.com', 'file:///tmp/model', None,
        ):
            with self.subTest(origin=origin):
                self.assert_stage('endpoint', lambda: cockpit.endpoint(origin))

    def test_request_is_bounded_and_supplies_schema_without_tools_or_truth(self):
        case = self.by_id['missing_field']
        request = cockpit.build_request(case, MODEL, max_output_tokens=512)
        self.assertEqual(request['model'], MODEL)
        self.assertEqual(request['max_tokens'], 512)
        self.assertFalse(request['stream'])
        self.assertNotIn('tools', request)
        self.assertTrue(request['response_format']['json_schema']['strict'])
        self.assertEqual(request['response_format']['json_schema']['schema'], cockpit.finding_schema())
        user_source = request['messages'][1]['content']
        for number, line in enumerate(case.lines, 1):
            self.assertIn(f'{number}: {line}', user_source)
        self.assertNotIn('unresolved":', user_source)
        for tokens in (True, 0, 127, 8193, 512.0):
            self.assert_stage('token_bound', lambda: cockpit.build_request(case, MODEL, max_output_tokens=tokens))
        for model in ('', '  ', 'x' * 257, None):
            self.assert_stage('model_argument', lambda: cockpit.build_request(case, model))

    def test_false_permission_cannot_be_replaced_by_integer_zero(self):
        case = self.by_id['source_instructions']
        findings = findings_for(case)
        permission = next(fact for fact in findings['facts'] if fact['key'] == 'model_approval_permission')
        self.assertIs(permission['value'], False)
        permission['value'] = 0
        scores = cockpit.validate_findings(case, findings)
        self.assertTrue(scores['schema_valid'])
        self.assertEqual(scores['correct_values'], len(case.facts) - 1)
        self.assertFalse(scores['accepted'])

    def test_correct_values_require_exact_original_line_and_expected_location(self):
        case = self.by_id['literal_fields']
        for replacement in (
            {'line': 1, 'quote': case.lines[0] + ' '},
            {'line': 2, 'quote': case.lines[1]},
            {'line': 999, 'quote': case.lines[0]},
        ):
            with self.subTest(citation=replacement):
                findings = findings_for(case)
                findings['facts'][0]['citations'] = [replacement]
                scores = cockpit.validate_findings(case, findings)
                self.assertEqual(scores['correct_values'], len(case.facts))
                self.assertFalse(scores['all_citations_valid'])
                self.assertFalse(scores['accepted'])

    def test_unicode_normalization_and_duplicate_evidence_are_not_silently_repaired(self):
        case = self.by_id['unicode_quote']
        findings = findings_for(case)
        findings['facts'][0]['citations'][0]['quote'] = case.lines[0].replace('é', 'e\u0301')
        self.assertFalse(cockpit.validate_findings(case, findings)['accepted'])
        findings = findings_for(case)
        findings['facts'][0]['citations'] *= 2
        self.assertTrue(cockpit.validate_findings(case, findings)['schema_valid'])
        self.assertFalse(cockpit.validate_findings(case, findings)['accepted'])

    def test_missing_extra_duplicate_and_unresolved_fields_fail_independently(self):
        case = self.by_id['missing_field']
        original = findings_for(case)
        missing = copy.deepcopy(original)
        missing['facts'].pop()
        self.assertEqual(cockpit.validate_findings(case, missing)['missing_facts'], 1)
        extra = copy.deepcopy(original)
        extra['facts'].append({'key': 'invented', 'value': 'claim',
                               'citations': [{'line': 1, 'quote': case.lines[0]}]})
        self.assertEqual(cockpit.validate_findings(case, extra)['extra_facts'], 1)
        duplicate = copy.deepcopy(original)
        duplicate['facts'].append(copy.deepcopy(duplicate['facts'][0]))
        self.assertFalse(cockpit.validate_findings(case, duplicate)['schema_valid'])
        unresolved = copy.deepcopy(original)
        unresolved['unresolved'] = []
        scores = cockpit.validate_findings(case, unresolved)
        self.assertEqual(scores['correct_supported_facts'], len(case.facts))
        self.assertFalse(scores['unresolved_valid'])
        self.assertFalse(scores['accepted'])

    def test_schema_rejects_bool_version_line_and_extra_properties(self):
        case = self.cases[0]
        for mutate in (
            lambda value: value.update(schema_version=True),
            lambda value: value.update(approval='accepted'),
            lambda value: value['facts'][0]['citations'][0].update(line=True),
            lambda value: value['facts'][0].update(action='execute'),
            lambda value: value.update(unresolved=['a', 'a']),
        ):
            findings = findings_for(case)
            mutate(findings)
            self.assertFalse(cockpit.validate_findings(case, findings)['schema_valid'])

    def test_response_identity_finish_and_tools_are_not_trusted(self):
        case = self.cases[0]
        wrong_model = envelope_for(case, model='other-model')
        truncated = envelope_for(case)
        truncated['choices'][0]['finish_reason'] = 'length'
        tool_call = envelope_for(case)
        tool_call['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'write'}}]
        legacy_call = envelope_for(case)
        legacy_call['choices'][0]['message']['function_call'] = {'name': 'write'}
        wrong_role = envelope_for(case)
        wrong_role['choices'][0]['message']['role'] = 'user'
        for envelope, stage in (
            (wrong_model, 'model_identity'), (truncated, 'finish_reason'),
            (tool_call, 'tool_call'), (legacy_call, 'tool_call'),
            (wrong_role, 'response_shape'), (envelope_for(case, choices=[]), 'response_shape'),
        ):
            with self.subTest(stage=stage):
                self.assert_stage(stage, lambda: cockpit.inspect_response(cockpit.canonical(envelope), case, MODEL))

    def test_final_json_is_strict_and_bounded(self):
        case = self.cases[0]
        for content, stage in (
            ('', 'empty_final'), ('   ', 'empty_final'),
            ('x' * (cockpit.MAX_FINAL_CHARACTERS + 1), 'final_size'),
            ('{"schema_version":1,"schema_version":1}', 'json_duplicate_key'),
            ('```json\n{}\n```', 'json_parse'),
        ):
            envelope = envelope_for(case)
            envelope['choices'][0]['message']['content'] = content
            self.assert_stage(stage, lambda: cockpit.inspect_response(cockpit.canonical(envelope), case, MODEL))

    def test_mock_success_persists_private_exact_wire_bodies_and_sanitized_usage(self):
        captured = []

        def respond(request):
            captured.append(request)
            self.assertEqual(str(request.url), 'http://127.0.0.1:1234/v1/chat/completions')
            self.assertEqual(request.headers['accept-encoding'], 'identity')
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, content=cockpit.canonical(envelope_for(self.cases[0])))

        summary = self.run_mock(respond)
        self.assertEqual(summary['accepted_cases'], 1)
        self.assertEqual(summary['attempts'], 1)
        self.assertEqual(summary['retries'], 0)
        self.assertEqual(summary['tools_invoked'], 0)
        self.assertEqual(summary['savings'], 'unmeasured')
        self.assertFalse(summary['lesson_release'])
        self.assertFalse(summary['model_training'])
        output = self.root / 'trial'
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for path in output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        case_id = self.cases[0].case_id
        self.assertEqual((output / f'{case_id}.request.json').read_bytes(), captured[0].content)
        response = (output / f'{case_id}.response.body').read_bytes()
        self.assertEqual(summary['cases'][0]['response_sha256'], hashlib.sha256(response).hexdigest())
        self.assertEqual(summary['cases'][0]['usage']['provider_reported']['total_tokens'], 150)
        self.assertEqual(cockpit.read_json(output / 'summary.json'), summary)

    def test_truncated_output_retains_usage_without_claiming_success(self):
        envelope = envelope_for(self.cases[0])
        envelope['choices'][0]['finish_reason'] = 'length'
        envelope['choices'][0]['message'].update(content='', reasoning_content='private test reasoning')
        summary = self.run_mock(lambda _: httpx.Response(200, content=cockpit.canonical(envelope)))
        row = summary['cases'][0]
        self.assertEqual(row['error_stage'], 'finish_reason')
        self.assertEqual(row['finish_reason'], 'length')
        self.assertEqual(row['usage']['provider_reported']['completion_tokens'], 50)
        self.assertEqual(row['reasoning']['characters'], len('private test reasoning'))
        self.assertNotIn('private test reasoning', json.dumps(summary))
        self.assertIsNone(row['scores'])
        self.assertEqual(summary['accepted_cases'], 0)

    def test_invalid_final_json_keeps_provider_usage_and_exact_alias_evidence(self):
        envelope = envelope_for(self.cases[0])
        envelope['choices'][0]['message']['content'] = 'not JSON'
        summary = self.run_mock(lambda _: httpx.Response(200, content=cockpit.canonical(envelope)))
        row = summary['cases'][0]
        self.assertEqual(row['error_stage'], 'json_parse')
        self.assertTrue(row['runtime_identity']['exact_match'])
        self.assertEqual(row['usage']['provider_reported']['prompt_tokens'], 100)

    def test_unmatched_runtime_identity_does_not_echo_arbitrary_model_text(self):
        body = cockpit.canonical(envelope_for(self.cases[0], model='UNTRUSTED_SECRET_STRING'))
        summary = self.run_mock(lambda _: httpx.Response(200, content=body))
        self.assertEqual(summary['cases'][0]['error_stage'], 'model_identity')
        self.assertIsNone(summary['cases'][0]['runtime_identity']['returned'])
        self.assertNotIn('UNTRUSTED_SECRET_STRING', json.dumps(summary))

    def test_redirect_is_recorded_without_following_external_location(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(302, headers={'location': 'https://example.com/private'}, content=b'redirect')

        summary = self.run_mock(respond)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary['cases'][0]['error_stage'], 'http_status')
        self.assertEqual(summary['cases'][0]['http_status'], 302)

    def test_response_byte_cap_retains_only_bounded_prefix(self):
        summary = self.run_mock(lambda _: httpx.Response(200, content=b'x' * (cockpit.MAX_RESPONSE_BYTES + 1)))
        row = summary['cases'][0]
        self.assertEqual(row['error_stage'], 'response_size')
        self.assertEqual(row['response_bytes_retained'], cockpit.MAX_RESPONSE_BYTES)
        self.assertEqual((self.root / 'trial' / f'{self.cases[0].case_id}.response.body').stat().st_size,
                         cockpit.MAX_RESPONSE_BYTES)

    def test_nonidentity_compression_is_refused(self):
        summary = self.run_mock(lambda _: httpx.Response(
            200, headers={'content-encoding': 'gzip'}, content=b''))
        self.assertEqual(summary['cases'][0]['error_stage'], 'response_encoding')

    def test_elapsed_time_guard_refuses_late_body_without_sleeping(self):
        class BodyStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{}'

        ticks = iter((0.0, 2.0, 2.0))
        with patch.object(cockpit, 'time', SimpleNamespace(monotonic=lambda: next(ticks))):
            summary = self.run_mock(
                lambda _: httpx.Response(200, stream=BodyStream()), timeout_seconds=1)
        self.assertEqual(summary['cases'][0]['error_stage'], 'wall_timeout')
        self.assertEqual(summary['cases'][0]['elapsed_seconds'], 2.0)

    def test_transport_failures_are_sanitized_and_timeouts_stop_queued_work(self):
        for exception, stage in ((httpx.ReadTimeout, 'transport_timeout'),
                                 (httpx.ConnectError, 'transport_error')):
            calls = []

            def respond(request):
                calls.append(request)
                if len(calls) == 1:
                    raise exception('SENSITIVE_PROVIDER_DETAIL', request=request)
                return httpx.Response(200, content=cockpit.canonical(envelope_for(self.cases[1])))

            summary = self.run_mock(respond, cases=self.cases[:2], name=stage)
            self.assertEqual(len(calls), 1 if exception is httpx.ReadTimeout else 2)
            self.assertEqual(summary['cases'][0]['error_stage'], stage)
            if exception is httpx.ReadTimeout:
                self.assertEqual(summary['unattempted_cases'], [self.cases[1].case_id])
                self.assertEqual(summary['batch_status'], 'stopped_after_timeout')
                self.assertFalse((self.root / stage / f'{self.cases[1].case_id}.request.json').exists())
            else:
                self.assertTrue(summary['cases'][1]['scores']['accepted'])
                self.assertEqual(summary['batch_status'], 'completed')
            self.assertNotIn('SENSITIVE_PROVIDER_DETAIL', json.dumps(summary))

    def test_usage_refuses_bool_negative_and_unbounded_counts(self):
        for usage in ({'prompt_tokens': True}, {'completion_tokens': -1}, {'total_tokens': 2**64},
                      {'completion_tokens_details': {'reasoning_tokens': False}}, []):
            envelope = envelope_for(self.cases[0], usage=usage)
            self.assert_stage('usage_shape', lambda: cockpit.response_metadata(cockpit.canonical(envelope), MODEL))
        metadata = cockpit.response_metadata(cockpit.canonical(envelope_for(self.cases[0], usage=None)), MODEL)
        self.assertEqual(metadata['usage']['availability'], 'unavailable')

    def test_invalid_bounds_and_output_reuse_do_not_make_requests(self):
        def unexpected(_):
            self.fail('Invalid arguments must fail before generation')

        for number in (True, 0, 301, float('nan'), float('inf')):
            self.assert_stage('timeout_argument', lambda: self.run_mock(unexpected, timeout_seconds=number))
        output = self.root / 'trial'
        output.mkdir()
        self.assert_stage('output_fresh_required', lambda: self.run_mock(unexpected))
        self.assert_stage('output_path', lambda: cockpit.write_prompt_package(self.cases, Path('relative')))
        linked = self.root / 'linked'
        linked.symlink_to(self.root, target_is_directory=True)
        self.assert_stage('output_path', lambda: cockpit.write_prompt_package(self.cases, linked / 'new'))

    def test_native_package_does_not_generate_or_contain_expected_findings(self):
        receipt = cockpit.write_prompt_package(self.cases, self.root / 'package')
        self.assertEqual(receipt['cases'], 6)
        self.assertFalse(receipt['generation_performed'])
        body = (self.root / 'package' / 'prompt-package.json').read_bytes()
        self.assertEqual(receipt['package_sha256'], hashlib.sha256(body).hexdigest())
        package = cockpit.parse_json(body)
        self.assertEqual({row['case_id'] for row in package['cases']}, set(self.by_id))
        for row in package['cases']:
            self.assertEqual(set(row), {'case_id', 'source_utf8_sha256', 'messages'})
            self.assertEqual(len(row['messages']), 2)

    def native_envelope(self):
        return {'schema_version': 1, 'results': [
            {'case_id': case.case_id, 'findings': findings_for(case)} for case in self.cases]}

    def test_native_results_require_explicit_unverified_identity_and_usage(self):
        supplied = cockpit.canonical(self.native_envelope())
        summary = cockpit.evaluate_external(self.cases, supplied, self.root / 'native', model='operator-selection')
        self.assertEqual(summary['accepted_cases'], 6)
        self.assertEqual(summary['attempts'], 0)
        self.assertIsNone(summary['tools_invoked'])
        self.assertIsNone(summary['native_fallbacks'])
        self.assertFalse(summary['lesson_release'])
        for row in summary['cases']:
            self.assertEqual(row['runtime_identity']['availability'], 'not_verified')
            self.assertIsNone(row['runtime_identity']['exact_match'])
            self.assertEqual(row['usage']['availability'], 'unavailable')
        self.assertEqual((self.root / 'native' / 'native-findings.json').read_bytes(), supplied)

    def test_native_duplicate_missing_unknown_and_cli_envelopes_are_refused(self):
        original = self.native_envelope()
        duplicate = copy.deepcopy(original)
        duplicate['results'][0]['case_id'] = duplicate['results'][1]['case_id']
        missing = copy.deepcopy(original)
        missing['results'].pop()
        unknown = copy.deepcopy(original)
        unknown['results'][0]['case_id'] = 'invented'
        bool_version = copy.deepcopy(original)
        bool_version['schema_version'] = True
        cli = {'status': 'success', 'structured_output': original}
        for envelope in (duplicate, missing, unknown, bool_version, cli):
            self.assert_stage('native_envelope', lambda: cockpit.evaluate_external(
                self.cases, cockpit.canonical(envelope), self.root / 'unused'))
        self.assertFalse((self.root / 'unused').exists())

    def test_native_valid_envelope_with_false_findings_records_failure(self):
        envelope = self.native_envelope()
        envelope['results'][0]['findings']['facts'][0]['value'] = 'fabricated project'
        summary = cockpit.evaluate_external(
            self.cases, cockpit.canonical(envelope), self.root / 'failed-native')
        self.assertEqual(summary['accepted_cases'], 5)
        self.assertFalse(summary['cases'][0]['scores']['accepted'])
        self.assertEqual(summary['cases'][0]['scores']['correct_values'], len(self.cases[0].facts) - 1)

    def test_lmstudio_request_preserves_source_and_rules_with_explicit_reasoning_off(self):
        for case in self.cases:
            with self.subTest(case=case.case_id):
                comparison = cockpit.build_request(case, MODEL, max_output_tokens=512)
                request = cockpit.build_native_request(case, MODEL, max_output_tokens=512)
                self.assertEqual(request['system_prompt'], comparison['messages'][0]['content'])
                self.assertEqual(request['input'], comparison['messages'][1]['content'])
                self.assertEqual(request['model'], MODEL)
                self.assertEqual(request['integrations'], [])
                self.assertEqual(request['reasoning'], 'off')
                self.assertFalse(request['store'])
                self.assertFalse(request['stream'])
                self.assertEqual(request['max_output_tokens'], 512)
                self.assertNotIn('response_format', request)
                self.assertNotIn('tools', request)
                self.assertNotIn('messages', request)

    def test_lmstudio_response_accepts_one_supported_final_with_zero_reported_reasoning(self):
        body = cockpit.canonical(lmstudio_envelope_for(self.cases[0]))
        scores, metadata = cockpit.inspect_native_response(body, self.cases[0], MODEL, max_output_tokens=512)
        self.assertTrue(scores['accepted'])
        self.assertTrue(metadata['runtime_identity']['exact_match'])
        self.assertEqual(metadata['usage']['provider_reported']['reasoning_output_tokens'], 0)
        self.assertEqual(metadata['finish_reason'], 'not_reported_by_native_api')
        self.assertEqual(metadata['runtime_identity']['weights_and_quantization_identity'], 'not_established')

    def test_lmstudio_response_refuses_model_mismatch_tools_and_unknown_output_types(self):
        case = self.cases[0]
        mismatched = lmstudio_envelope_for(case, model_instance_id='unverified-other-runtime')
        for envelope, stage in (
            (mismatched, 'model_identity'),
            (lmstudio_envelope_for(case, output=[{'type': 'tool_call', 'tool': 'write'}]), 'tool_call'),
            (lmstudio_envelope_for(case, output=[{'type': 'invalid_tool_call'}]), 'tool_call'),
            (lmstudio_envelope_for(case, output=[{'type': 'image', 'content': 'unknown'}]), 'response_shape'),
            (lmstudio_envelope_for(case, output=[None]), 'response_shape'),
            (lmstudio_envelope_for(case, output=[]), 'response_shape'),
        ):
            with self.subTest(stage=stage, output=envelope.get('output')):
                self.assert_stage(stage, lambda: cockpit.inspect_native_response(
                    cockpit.canonical(envelope), case, MODEL, max_output_tokens=512))

    def test_lmstudio_requires_single_nonempty_strict_json_final(self):
        case = self.cases[0]
        valid_message = lmstudio_envelope_for(case)['output'][0]
        for output, stage in (
            ([valid_message, copy.deepcopy(valid_message)], 'empty_final'),
            ([{'type': 'message', 'content': ' '}], 'empty_final'),
            ([{'type': 'message', 'content': None}], 'empty_final'),
            ([{'type': 'message', 'content': '```json\n{}\n```'}], 'json_parse'),
            ([{'type': 'message', 'content': '{"x":1,"x":2}'}], 'json_duplicate_key'),
            ([{'type': 'message', 'content': 'x' * (cockpit.MAX_FINAL_CHARACTERS + 1)}], 'final_size'),
        ):
            with self.subTest(stage=stage):
                envelope = lmstudio_envelope_for(case, output=output)
                self.assert_stage(stage, lambda: cockpit.inspect_native_response(
                    cockpit.canonical(envelope), case, MODEL, max_output_tokens=512))

    def test_lmstudio_refuses_reasoning_text_even_when_reported_reasoning_tokens_are_zero(self):
        case = self.cases[0]
        envelope = lmstudio_envelope_for(case)
        envelope['output'].insert(0, {'type': 'reasoning', 'content': 'synthetic hidden deliberation'})
        self.assert_stage('reasoning_off_not_observed', lambda: cockpit.inspect_native_response(
            cockpit.canonical(envelope), case, MODEL, max_output_tokens=512))
        envelope = lmstudio_envelope_for(case)
        envelope['stats']['reasoning_output_tokens'] = 1
        self.assert_stage('reasoning_off_not_observed', lambda: cockpit.inspect_native_response(
            cockpit.canonical(envelope), case, MODEL, max_output_tokens=512))
        envelope['stats'].pop('reasoning_output_tokens')
        self.assert_stage('reasoning_off_not_observed', lambda: cockpit.inspect_native_response(
            cockpit.canonical(envelope), case, MODEL, max_output_tokens=512))

    def test_lmstudio_usage_and_completion_boundary_require_bounded_integer_counts(self):
        case = self.cases[0]
        for count in (512, 513, None):
            envelope = lmstudio_envelope_for(case)
            if count is None:
                envelope['stats'].pop('total_output_tokens')
            else:
                envelope['stats']['total_output_tokens'] = count
            self.assert_stage('native_completion_bound', lambda: cockpit.inspect_native_response(
                cockpit.canonical(envelope), case, MODEL, max_output_tokens=512))
        for stats in (None, [], {'input_tokens': True}, {'total_output_tokens': -1},
                      {'reasoning_output_tokens': 1.0}, {'total_output_tokens': 2**64}):
            self.assert_stage('usage_shape', lambda: cockpit.native_metadata(
                cockpit.canonical(lmstudio_envelope_for(case, stats=stats)), MODEL))

    def test_lmstudio_mock_uses_native_url_and_labels_distinct_diagnostic_conditions(self):
        captured = []

        def respond(request):
            captured.append(request)
            self.assertEqual(str(request.url), 'http://127.0.0.1:1234/api/v1/chat')
            wire = cockpit.parse_json(request.content)
            self.assertEqual(wire['reasoning'], 'off')
            self.assertFalse(wire['store'])
            self.assertEqual(wire['integrations'], [])
            return httpx.Response(200, content=cockpit.canonical(lmstudio_envelope_for(self.cases[0])))

        summary = self.run_mock(respond, adapter='loopback_lmstudio_reasoning_off', max_output_tokens=512)
        self.assertEqual(len(captured), 1)
        self.assertEqual(summary['accepted_cases'], 1)
        self.assertEqual(summary['attempts'], 1)
        self.assertEqual(summary['adapter'], 'loopback_lmstudio_reasoning_off')
        self.assertEqual(summary['output_constraint'], 'prompt_only')
        self.assertEqual(summary['reasoning_control'], 'off')
        self.assertEqual(summary['cases'][0]['usage']['provider_reported']['total_output_tokens'], 50)
        self.assertEqual(summary['cases'][0]['usage']['provider_reported']['reasoning_output_tokens'], 0)

    def test_lmstudio_failure_retains_usage_and_reasoning_size_without_exposing_text(self):
        envelope = lmstudio_envelope_for(self.cases[0])
        envelope['output'].insert(0, {'type': 'reasoning', 'content': 'PRIVATE_SYNTHETIC_REASONING'})
        envelope['stats'].update(reasoning_output_tokens=7, unrecognized_detail='SECRET_METADATA')
        summary = self.run_mock(lambda _: httpx.Response(200, content=cockpit.canonical(envelope)),
                                adapter='loopback_lmstudio_reasoning_off', max_output_tokens=512)
        row = summary['cases'][0]
        self.assertEqual(row['error_stage'], 'reasoning_off_not_observed')
        self.assertEqual(row['usage']['provider_reported']['reasoning_output_tokens'], 7)
        self.assertEqual(row['reasoning']['characters'], len('PRIVATE_SYNTHETIC_REASONING'))
        self.assertNotIn('PRIVATE_SYNTHETIC_REASONING', json.dumps(summary))
        self.assertNotIn('SECRET_METADATA', json.dumps(summary))
        self.assertIsNone(row['scores'])
        self.assertEqual(summary['accepted_cases'], 0)

    def test_lmstudio_budget_boundary_failure_retains_native_usage(self):
        envelope = lmstudio_envelope_for(self.cases[0])
        envelope['stats']['total_output_tokens'] = 512
        summary = self.run_mock(lambda _: httpx.Response(200, content=cockpit.canonical(envelope)),
                                adapter='loopback_lmstudio_reasoning_off', max_output_tokens=512)
        row = summary['cases'][0]
        self.assertEqual(row['error_stage'], 'native_completion_bound')
        self.assertEqual(row['usage']['provider_reported']['total_output_tokens'], 512)
        self.assertEqual(row['finish_reason'], 'not_reported_by_native_api')
        self.assertIsNone(row['scores'])

    def test_unknown_adapter_is_refused_before_generation_or_output_creation(self):
        def unexpected(_):
            self.fail('Unknown adapters must not generate')

        for adapter in ('unsupported', 'explicit_native_findings_json', None):
            self.assert_stage('adapter_argument', lambda: self.run_mock(unexpected, adapter=adapter))
        self.assertFalse((self.root / 'trial').exists())


if __name__ == '__main__':
    unittest.main()
