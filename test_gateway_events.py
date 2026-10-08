"""Offline internal event-contract acceptance; no routing or services."""
import hashlib
import unittest
from gateway.events import canonical, validate_event


class EventTests(unittest.TestCase):
    def event(self, payload=None):
        payload = payload if payload is not None else {'fixture': 'safe'}
        return dict(schema_version=1, event_id='event', scope='project', request_id='request',
                    type='response.completed', captured_at=1, payload=payload,
                    payload_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())

    def test_identity_and_detachment(self):
        event = self.event()
        result = validate_event(event)
        result['payload']['fixture'] = 'changed'
        self.assertEqual(event['payload']['fixture'], 'safe')
        event['payload']['fixture'] = 'tampered'
        with self.assertRaises(ValueError):
            validate_event(event)

    def test_invalid_contract_and_utf8_budget(self):
        for field, value in [('schema_version', True), ('captured_at', True),
                              ('type', 'execute.payment'), ('scope', ''),
                              ('payload_sha256', 'wrong')]:
            event = self.event()
            event[field] = value
            with self.assertRaises(ValueError):
                validate_event(event)
        for payload in [{'fixture': 'é' * 5000}, {'value': float('nan')}]:
            with self.assertRaises(ValueError):
                validate_event(self.event(payload))
        event = self.event()
        event['authorization'] = 'secret'
        with self.assertRaises(ValueError):
            validate_event(event)


if __name__ == '__main__':
    unittest.main()
