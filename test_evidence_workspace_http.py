"""Offline local HTTP evidence acceptance, using synthetic isolated temporary state.

No environment variables, real sockets, services or personal records are required.
Run with .venv/bin/python test_evidence_workspace_http.py.
"""

import base64
import gzip
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

import httpx

from graphrag_core.evidence_workspace import server
from test_evidence_workspace import AMENDMENT, ORIGINAL, package


HOST = "127.0.0.1:8765"
ORIGIN = "http://" + HOST
TOKEN = "synthetic-test-capability-0123456789abcdef"
AUTHORIZATION = ("authorization", "Bearer " + TOKEN)


class EvidenceWorkspaceHttpAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.app = server.create_app(str(self.root / "workspace"), TOKEN, HOST)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 54321),
                                         raise_app_exceptions=False),
            base_url=ORIGIN,
        )
        self.addAsyncCleanup(self.client.aclose)

    async def request(self, method, path, *, authorized=True, headers=(), **kwargs):
        request_headers = [AUTHORIZATION] if authorized else []
        request_headers.extend(headers)
        return await self.client.request(method, path, headers=request_headers, **kwargs)

    async def import_fixture(self):
        response = await self.request("POST", "/api/import", json=package())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assert_private_response(self, response):
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(response.headers.get("x-frame-options"), "DENY")
        self.assertEqual(response.headers.get("cross-origin-resource-policy"), "same-origin")
        self.assertIn("frame-ancestors 'none'", response.headers.get("content-security-policy", ""))
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertNotIn(TOKEN, response.text)

    async def test_full_checked_review_export_and_recovery_workflow(self):
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})
        imported = await self.import_fixture()
        self.assertEqual(imported["revision"], 1)
        page = await self.request("GET", "/api/cases/fixture-case/sources/message?start_line=1&max_lines=1")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["numbered_lines"], [{"number": 1, "text": "Collected café record 😀"}])
        self.assertEqual(page.json()["next_line"], 2)
        checked = await self.request("POST", "/api/cases/fixture-case/checks", json={
            "source_id": "message", "expected_revision": 1,
            "content_base64": base64.b64encode(ORIGINAL.encode()).decode(),
        })
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(checked.json()["revision"], 2)
        self.assertEqual(checked.json()["freshness"], "matches-supplied-bytes")
        review = await self.request("POST", "/api/cases/fixture-case/reviews", json={
            "relationship_id": "request", "decision": "amended", "reviewer": "synthetic reviewer",
            "reason": "The source establishes a request, not ownership.", "expected_revision": 2,
            "statement": AMENDMENT,
        })
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(review.json()["revision"], 3)
        stale = await self.request("POST", "/api/cases/fixture-case/reviews", json={
            "relationship_id": "request", "decision": "accepted", "reviewer": "stale reviewer",
            "reason": "Must not override a newer review.", "expected_revision": 1,
        })
        self.assertEqual(stale.status_code, 409)
        report = await self.request("GET", "/api/cases/fixture-case/report")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertIn(AMENDMENT, report.text)
        self.assertNotIn("Collected café record", report.text)
        self.assertNotIn("The sender requested a payment.", report.text)
        self.assertIn('attachment; filename="fixture-case-reviewed-report.md"',
                      report.headers["content-disposition"])
        self.assertTrue(report.headers["content-type"].startswith("text/markdown"))
        self.assert_private_response(report)
        backup = await self.request("GET", "/api/cases/fixture-case/backup")
        self.assertEqual(backup.status_code, 200)
        self.assertEqual(backup.headers["content-type"], "application/zip")
        self.assertIn("private-recovery.zip", backup.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
            self.assertEqual(archive.read("originals/message"), ORIGINAL.encode())
        restored = await self.request("POST", "/api/restore", content=backup.content, headers=[
            ("content-type", "application/zip"), ("x-restore-case-id", "restored-fixture"),
        ])
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["revision"], 3)
        detail = await self.request("GET", "/api/cases/restored-fixture")
        self.assertEqual(detail.json()["origin_case_id"], "fixture-case")
        self.assertEqual(detail.json()["sources"][0]["freshness"], "matches-supplied-bytes")
        original = await self.request("GET", "/api/cases/restored-fixture/sources/message/content")
        self.assertEqual(original.content, ORIGINAL.encode())
        repeated = await self.request("POST", "/api/restore", content=backup.content, headers=[
            ("content-type", "application/zip"), ("x-restore-case-id", "restored-fixture"),
        ])
        self.assertEqual(repeated.status_code, 409)

    async def test_original_content_is_exact_and_carries_private_security_headers(self):
        await self.import_fixture()
        response = await self.request("GET", "/api/cases/fixture-case/sources/message/content")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, ORIGINAL.encode())
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(int(response.headers["content-length"]), len(ORIGINAL.encode()))
        self.assert_private_response(response)
        denied = await self.request("GET", "/api/cases/fixture-case/sources/message/content", authorized=False)
        self.assertEqual(denied.status_code, 401)
        self.assertNotIn("Collected café record", denied.text)
        self.assert_private_response(denied)

    async def test_exact_host_rejects_alternative_names_and_duplicate_headers(self):
        for host in ("localhost:8765", "127.0.0.1", "127.0.0.1:8766", "127.0.0.1:8765.attacker.invalid"):
            with self.subTest(host=host):
                response = await self.request("GET", "/api/cases", headers=[("host", host)])
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"], "local-host-required")
                self.assert_private_response(response)
        duplicate = await self.request("GET", "/api/cases", headers=[("host", HOST), ("host", HOST)])
        self.assertEqual(duplicate.status_code, 403)
        self.assert_private_response(duplicate)

    async def test_origin_is_optional_for_local_clients_but_exact_if_present(self):
        self.assertEqual((await self.request("GET", "/api/cases")).status_code, 200)
        good = await self.request("GET", "/api/cases", headers=[("origin", ORIGIN)])
        self.assertEqual(good.status_code, 200)
        for origin in ("null", "https://" + HOST, "http://localhost:8765", "https://attacker.invalid"):
            with self.subTest(origin=origin):
                denied = await self.request("GET", "/api/cases", headers=[("origin", origin)])
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.json()["error"], "same-origin-required")
                self.assert_private_response(denied)
        duplicate = await self.request("GET", "/api/cases", headers=[("origin", ORIGIN), ("origin", ORIGIN)])
        self.assertEqual(duplicate.status_code, 403)

    async def test_nonloopback_client_cannot_read_static_live_or_api(self):
        for client_address in ("203.0.113.5", "192.168.1.10", "not-an-ip"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, client=(client_address, 12345),
                                             raise_app_exceptions=False), base_url=ORIGIN,
            ) as client:
                for path in ("/", "/live", "/api/cases"):
                    with self.subTest(client=client_address, path=path):
                        response = await client.get(path, headers=[AUTHORIZATION])
                        self.assertEqual(response.status_code, 403)
                        self.assert_private_response(response)

    async def test_capability_is_required_and_not_accepted_from_query_cookie_or_duplicates(self):
        for path, headers in (
            ("/api/cases", []),
            ("/api/cases?token=" + TOKEN, []),
            ("/api/cases?access_token=" + TOKEN, []),
            ("/api/cases", [("cookie", "token=" + TOKEN)]),
            ("/api/cases", [("authorization", "Bearer wrong-token")]),
            ("/api/cases", [("authorization", TOKEN)]),
            ("/api/cases", [AUTHORIZATION, AUTHORIZATION]),
        ):
            with self.subTest(path=path, headers=headers):
                response = await self.request("GET", path, authorized=False, headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"], "local-capability-required")
                self.assert_private_response(response)

    async def test_nonascii_authorization_is_denied_without_server_error(self):
        response = await self.request("GET", "/api/cases", authorized=False,
                                      headers=[(b"authorization", b"Bearer \xff")])
        self.assertEqual(response.status_code, 401)
        self.assert_private_response(response)

    async def test_missing_or_wrong_media_and_compressed_requests_are_rejected(self):
        encoded = json.dumps(package()).encode()
        for headers, content in (
            ([], encoded),
            ([("content-type", "text/plain")], encoded),
            ([("content-type", "application/json"), ("content-encoding", "gzip")], gzip.compress(encoded)),
            ([("content-type", "application/json"), ("content-encoding", "br")], encoded),
        ):
            with self.subTest(headers=headers):
                response = await self.request("POST", "/api/import", headers=headers, content=content)
                self.assertEqual(response.status_code, 422)
                self.assert_private_response(response)
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})

    async def test_duplicate_json_and_nonfinite_invalid_utf8_or_nonobject_json_are_rejected(self):
        for content in (b'{"title":"one","title":"two"}', b'{"a":{"b":1,"b":2}}',
                        b'{"value":NaN}', b'{"value":Infinity}', b'\xff', b'[]', b'null', b'"text"'):
            with self.subTest(content=content):
                response = await self.request("POST", "/api/import", content=content,
                                              headers=[("content-type", "application/json")])
                self.assertEqual(response.status_code, 422)
                self.assert_private_response(response)
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})

    async def test_review_wrong_types_are_validation_errors_without_revision_change(self):
        await self.import_fixture()
        valid = {"relationship_id": "request", "decision": "accepted", "reviewer": "fixture-reviewer",
                 "reason": "Supports the source", "expected_revision": 1}
        for field, bad in (("decision", []), ("decision", {}), ("decision", None),
                           ("reviewer", []), ("reason", {}), ("relationship_id", []),
                           ("expected_revision", True), ("expected_revision", "1"),
                           ("expected_revision", [])):
            body = dict(valid, **{field: bad})
            with self.subTest(field=field, bad=bad):
                response = await self.request("POST", "/api/cases/fixture-case/reviews", json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assert_private_response(response)
        detail = await self.request("GET", "/api/cases/fixture-case")
        self.assertEqual(detail.json()["revision"], 1)

    async def test_package_wrong_types_are_validation_errors_without_partial_import(self):
        variants = []
        for field, bad in (("sources", {}), ("entities", None), ("relationships", "wrong"),
                           ("title", []), ("schema_version", True), ("coverage", [])):
            variants.append((field, dict(package(), **{field: bad})))
        for field, bad in (("media_type", []), ("id", {}), ("text", {})):
            body = package()
            body["sources"][0][field] = bad
            variants.append(("source " + field, body))
        for description, body in variants:
            with self.subTest(description=description):
                response = await self.request("POST", "/api/import", json=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})

    async def test_source_checks_reject_bad_types_and_ambiguous_inputs(self):
        await self.import_fixture()
        valid = {"source_id": "message", "expected_revision": 1,
                 "content_base64": base64.b64encode(ORIGINAL.encode()).decode()}
        variants = [dict(valid, missing=True), dict(valid, missing="true"), dict(valid, content_base64=[]),
                    dict(valid, source_id={}), dict(valid, expected_revision=False),
                    {"source_id": "message", "expected_revision": 1},
                    {"source_id": "message", "expected_revision": 1, "missing": []}]
        for body in variants:
            with self.subTest(body=body):
                response = await self.request("POST", "/api/cases/fixture-case/checks", json=body)
                self.assertEqual(response.status_code, 422, response.text)
        detail = await self.request("GET", "/api/cases/fixture-case")
        self.assertEqual(detail.json()["revision"], 1)
        self.assertEqual(detail.json()["sources"][0]["freshness"], "not-checked")

    async def test_source_pagination_and_unknown_ids_are_bounded(self):
        await self.import_fixture()
        for query in ("max_lines=201", "max_lines=0", "start_line=4", "start_line=0",
                      "start_line=nan", "max_lines=1.5"):
            with self.subTest(query=query):
                response = await self.request("GET", "/api/cases/fixture-case/sources/message?" + query)
                self.assertEqual(response.status_code, 422)
        for path in ("/api/cases/unknown", "/api/cases/fixture-case/sources/unknown",
                     "/api/cases/fixture-case/sources/unknown/content", "/api/cases/unknown/report",
                     "/api/cases/unknown/backup"):
            with self.subTest(path=path):
                response = await self.request("GET", path)
                self.assertEqual(response.status_code, 422)
                self.assert_private_response(response)

    async def test_request_stream_byte_budget_is_enforced_before_storage(self):
        async def oversized_stream():
            for _ in range(49):
                yield b"x" * (1024 * 1024)

        response = await self.request("POST", "/api/import", content=oversized_stream(),
                                      headers=[("content-type", "application/json")])
        self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})

    async def test_storage_budgets_and_invalid_recovery_are_safe_http_errors(self):
        oversized = package()
        oversized["sources"][0]["text"] = "x" * (256 * 1024 + 1)
        oversized["sources"][0].pop("sha256")
        response = await self.request("POST", "/api/import", json=oversized)
        self.assertEqual(response.status_code, 422)
        restored = await self.request("POST", "/api/restore", content=b"bad ZIP",
                                      headers=[("content-type", "application/zip")])
        self.assertEqual(restored.status_code, 422)
        self.assertEqual(restored.json()["error"], "evidence-integrity-check-failed")
        self.assertNotIn(str(self.root), restored.text)
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})

    async def test_corrupted_original_blocks_all_download_surfaces(self):
        await self.import_fixture()
        db = sqlite3.connect(self.root / "workspace" / "workspaces.sqlite3")
        try:
            with db:
                db.execute("UPDATE sources SET content=? WHERE case_id=? AND source_id=?",
                           (b"Corrupted fixture", "fixture-case", "message"))
        finally:
            db.close()
        for path in ("/api/cases/fixture-case", "/api/cases/fixture-case/sources/message/content",
                     "/api/cases/fixture-case/report", "/api/cases/fixture-case/backup"):
            with self.subTest(path=path):
                response = await self.request("GET", path)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"], "evidence-integrity-check-failed")
                self.assert_private_response(response)

    async def test_local_liveness_is_public_but_does_not_reveal_case_or_capability(self):
        response = await self.request("GET", "/live", authorized=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "surface": "local-evidence-workspace"})
        self.assert_private_response(response)
        denied = await self.request("POST", "/api/demo", authorized=False)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual((await self.request("GET", "/api/cases")).json(), {"cases": []})


if __name__ == "__main__":
    unittest.main()
