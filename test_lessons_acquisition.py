"""Offline acquisition contracts; no remote services or private records required."""

import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from graphrag_core.lessons_corpus.acquire import (
    AcquisitionRefused,
    _Response,
    _DNS_CACHE_SECONDS,
    _DOH_MAX_BYTES,
    _DoHNoRedirect,
    _SYNTHETIC_DNS_CACHE,
    _SafeRedirect,
    _URLPolicy,
    _document,
    _lookup_public_dns,
    _resolve_public_host,
    capture_wordpress,
)

BASE = "https://lessons.example"


def response(url, body, status=200, media_type="application/json", headers=None):
    if not isinstance(body, bytes):
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return _Response(url, status, {"content-type": media_type, **(headers or {})}, body)


class FakeWordPress:
    def __init__(self):
        self.urls = []
        self.calls = {}
        self.drift = False
        self.undated_content_drift = False
        self.http_failure = False
        self.duplicate_id = False
        self.bad_pagination = False
        self.bad_xml = False
        self.redirect_outside = False
        self.wrong_pdf = False
        self.omit_page_links = False
        self.additional_break = False
        self.large_pagination_links = False
        self.html_error_mode = None
        self.media_caption_pdf_links = False

    def fetch(self, url, limit):
        self.urls.append(url)
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path
        self.calls[path] = self.calls.get(path, 0) + 1
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/wp-json/wp/v2/taxonomies":
            return response(url, {"series": {"rest_base": "article-series"}})
        if path.startswith("/wp-json/wp/v2/"):
            endpoint = path.rsplit("/", 1)[-1]
            page = int(query["page"][0])
            if endpoint == "posts":
                obj_id = 1 if page == 1 or self.duplicate_id else 2
                changed = self.drift and self.calls[path] > 2
                embedded = base64.b64encode(json.dumps({"url": BASE + "/source.pdf"}).encode()).decode()
                content = ('<p>Words &amp; more words.</p><!--nextpage-->'
                           '<a href="https://external.example/book">Reference</a>'
                           '<a href="/wp-admin/edit.php">Admin</a>'
                           '<a href="/photo.zip">Archive</a>'
                           '<a href="/article/2/">Second part</a>'
                           '<a href="https://[">Malformed</a>'
                           f'<iframe src="/?pdfemb-data={embedded}"></iframe>')
                if self.omit_page_links:
                    content = content.replace('<a href="/article/2/">Second part</a>', "")
                if self.additional_break:
                    content += "<!--nextpage--><p>Final part</p>"
                obj = {"id": obj_id, "link": BASE + f"/article-{obj_id}/", "author": 5,
                       "title": {"rendered": "Original &amp; Title"},
                       "content": {"rendered": content}, "excerpt": {"rendered": "<p>Excerpt</p>"},
                       "date_gmt": "2025-01-01T00:00:00",
                       "modified_gmt": "2026-01-02T00:00:00" if changed else "2026-01-01T00:00:00",
                       "custom_metadata": {"must_keep": "complete object"}}
                return response(url, [obj], headers={} if self.bad_pagination else {
                    "x-wp-total": "2", "x-wp-totalpages": "2"})
            objects = {
                "pages": [{"id": 9, "link": BASE + "/about/", "title": {"rendered": "About"},
                           "content": {"rendered": "<p>About words</p>"}}],
                "comments": [{"id": 7, "post": 1, "link": BASE + "/article-1/#comment-7",
                              "author_name": "Public commenter", "content": {"rendered": "<p>Reply</p>"}}],
                "media": [{"id": 3, "link": BASE + "/media/", "source_url": BASE + "/source.pdf",
                           "caption": {"rendered": "<p>Caption</p>"}, "alt_text": "Alternative text",
                           "description": {"rendered": "<p>Full media description</p>"}}],
                "categories": [{"id": 1, "link": BASE + "/category/lesson/", "name": "Lessons",
                                "description": "<p>Category description</p>"}],
                "tags": [], "users": [{"id": 5, "link": BASE + "/author/writer/", "name": "Writer"}],
                "article-series": [{"id": 4, "link": BASE + "/series/learning/", "name": "Learning"}],
            }.get(endpoint, [])
            if endpoint == "comments" and self.undated_content_drift and self.calls[path] > 1:
                objects[0]["content"]["rendered"] = "<p>Edited reply without modification date</p>"
            if endpoint == "media" and self.media_caption_pdf_links:
                objects[0]["caption"]["rendered"] += (
                    '<a href="/caption-only.pdf">Caption PDF</a>'
                    '<iframe src="/iframe-only.pdf"></iframe>'
                    '<embed src="/embed-only.pdf" type="application/pdf">'
                    '<object data="/object-only.pdf" type="application/pdf"></object>'
                    '<object data="https://external.example/object.pdf"></object>'
                    '<embed src="https://external.example/embed.pdf">'
                    '<iframe src="/excluded.zip"></iframe>')
            return response(url, objects, headers={"x-wp-total": str(len(objects)),
                                                    "x-wp-totalpages": "1" if objects else "0"})
        if path == "/robots.txt":
            return response(url, b"User-agent: *\nDisallow:\nSitemap: https://lessons.example/sitemap_index.xml\n",
                            media_type="text/plain; charset=UTF-8")
        if path == "/sitemap_index.xml":
            body = b'<!DOCTYPE bad [<!ENTITY x "boom">]><urlset></urlset>' if self.bad_xml else (
                b'<sitemapindex><sitemap><loc>https://lessons.example/post-sitemap.xml</loc>'
                b'</sitemap></sitemapindex>')
            return response(url, body, media_type="text/xml")
        if path == "/post-sitemap.xml":
            return response(url, b'<urlset><url><loc>https://lessons.example/orphan/</loc></url>'
                            b'<url><loc>https://external.example/outside/</loc></url></urlset>', media_type="text/xml")
        if path in ("/source.pdf", "/caption-only.pdf", "/iframe-only.pdf", "/embed-only.pdf", "/object-only.pdf"):
            return response(url, b"wrong" if self.wrong_pdf else b"%PDF-1.4\nOriginal PDF bytes\n",
                            media_type="application/pdf")
        if path == "/orphan/" and self.http_failure:
            return response(url, b"Missing original", status=404, media_type="text/html")
        if path == "/orphan/" and self.redirect_outside:
            return response("https://external.example/redirected/", b"outside", media_type="text/html")
        if path == "/orphan/" and self.html_error_mode == "timeout":
            raise TimeoutError("offline simulated timeout")
        if path == "/orphan/" and self.html_error_mode == "media_type":
            return response(url, b"{}", media_type="application/json")
        if path == "/orphan/" and self.html_error_mode == "encoding":
            return response(url, b"\xff", media_type="text/html; charset=UTF-8")
        page_links = b"" if self.omit_page_links else b'<a href="/article/2/">Part</a>'
        if self.large_pagination_links:
            page_links += b'<a href="/page/10/">Ten</a><a href="/page/100/">One hundred</a>'
        return response(url, b'<html><main>Public HTML words</main>' + page_links + b'</html>',
                        media_type="text/html; charset=UTF-8")


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir()).resolve()
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "capture"
        self.network = FakeWordPress()
        self.resolver = patch("graphrag_core.lessons_corpus.acquire._resolve_public_host")
        self.resolver.start()
        self.addCleanup(self.resolver.stop)
        self.transport = patch("graphrag_core.lessons_corpus.acquire._Transport.fetch", side_effect=self.network.fetch)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def capture(self, **kwargs):
        return capture_wordpress(BASE + "/", self.output, rights_basis="Owner permission to archive", **kwargs)

    def rows(self, name):
        return [json.loads(line) for line in (self.output / name).read_text().splitlines()]

    def test_full_paginated_inventory_raw_fidelity_and_private_files(self):
        result = self.capture()
        self.assertTrue(result["coverage"]["complete_within_declared_scope"])
        counts = result["inventory"]["document_counts"]
        self.assertEqual(counts["post"], 2)
        self.assertEqual(counts["series"], 1)
        docs = self.rows("documents.jsonl")
        post = next(row for row in docs if row["id"] == "post:1")
        self.assertEqual(post["title_html"], "Original &amp; Title")
        self.assertIn("<!--nextpage-->", post["content_html"])
        self.assertEqual(post["metadata"]["custom_metadata"]["must_keep"], "complete object")
        self.assertTrue(any(row["kind"] == "comment" for row in docs))
        self.assertIn(BASE + "/article/2/", self.network.urls)
        self.assertIn(BASE + "/orphan/", self.network.urls)
        self.assertIn(BASE + "/source.pdf", result["inventory"]["pdf_acquired"])
        self.assertFalse(any("external.example" in url for url in self.network.urls))
        self.assertFalse(any("wp-admin" in url or url.endswith(".zip") for url in self.network.urls))
        for row in self.rows("acquisitions.jsonl"):
            if row["raw_path"]:
                raw = (self.output / row["raw_path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row["body_sha256"])
        for path in self.output.rglob("*"):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_http_failure_body_and_manifest_retained(self):
        self.network.http_failure = True
        result = self.capture()
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])
        row = next(r for r in self.rows("acquisitions.jsonl") if r["requested_url"] == BASE + "/orphan/")
        self.assertEqual(row["status"], 404)
        self.assertEqual((self.output / row["raw_path"]).read_bytes(), b"Missing original")
        self.assertIn(BASE + "/orphan/", result["coverage"]["pending_html_urls"])

    def test_failed_html_remains_pending_without_repeated_attempts(self):
        for mode in ("timeout", "media_type", "encoding"):
            with self.subTest(mode=mode):
                self.network = FakeWordPress()
                self.network.html_error_mode = mode
                with patch("graphrag_core.lessons_corpus.acquire._Transport.fetch", side_effect=self.network.fetch):
                    result = capture_wordpress(BASE + "/", Path(self.temp.name) / mode, rights_basis="Permission")
                self.assertFalse(result["coverage"]["complete_within_declared_scope"])
                self.assertIn(BASE + "/orphan/", result["coverage"]["pending_html_urls"])
                self.assertEqual(self.network.urls.count(BASE + "/orphan/"), 1)

    def test_media_rendered_description_is_html_and_metadata_is_complete(self):
        self.capture()
        doc = next(row for row in self.rows("documents.jsonl") if row["id"] == "media:3")
        self.assertEqual(doc["content_html"], "<p>Full media description</p>")
        self.assertEqual(doc["metadata"]["caption"]["rendered"], "<p>Caption</p>")
        self.assertEqual(doc["metadata"]["alt_text"], "Alternative text")

    def test_caption_only_pdf_links_and_embedded_attributes_are_archived_without_scope_expansion(self):
        self.network.media_caption_pdf_links = True
        result = self.capture()
        for name in ("caption-only.pdf", "iframe-only.pdf", "embed-only.pdf", "object-only.pdf"):
            self.assertIn(BASE + "/" + name, result["inventory"]["pdf_targets"])
            self.assertIn(BASE + "/" + name, result["inventory"]["pdf_acquired"])
            self.assertIn(BASE + "/" + name, self.network.urls)
        self.assertFalse(any("external.example" in url for url in self.network.urls))
        self.assertNotIn(BASE + "/excluded.zip", self.network.urls)
        self.assertTrue(result["coverage"]["complete_within_declared_scope"])

    def test_page_breaks_discover_all_parts_without_theme_anchors(self):
        self.network.omit_page_links = True
        self.network.additional_break = True
        self.capture()
        for article in (1, 2):
            for page in (2, 3):
                self.assertIn(BASE + f"/article-{article}/{page}/", self.network.urls)

    def test_pagination_links_include_ten_and_one_hundred(self):
        self.network.large_pagination_links = True
        self.capture()
        self.assertIn(BASE + "/page/10/", self.network.urls)
        self.assertIn(BASE + "/page/100/", self.network.urls)

    def test_unique_url_bound_is_explicit(self):
        result = self.capture(max_urls=3)
        self.assertTrue(result["coverage"]["bound_reached"])
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])
        self.assertEqual(result["inventory"]["unique_requested_urls"], 3)
        self.assertTrue(result["coverage"]["refusals"])

    def test_total_byte_bound_is_explicit(self):
        result = self.capture(max_total_bytes=10)
        self.assertTrue(result["coverage"]["bound_reached"])
        self.assertLessEqual(result["inventory"]["response_bytes"], 10)
        self.assertTrue(any("byte bound" in r["reason"] for r in result["coverage"]["refusals"]))

    def test_mid_run_modified_signature_drift(self):
        self.network.drift = True
        result = self.capture()
        self.assertEqual(result["coverage"]["changed_endpoints"], ["post"])
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])

    def test_undated_content_change_is_detected_with_unchanged_ids_and_counts(self):
        self.network.undated_content_drift = True
        result = self.capture()
        initial = result["coverage"]["initial_rest_signatures"]["comment"]
        final = result["coverage"]["final_rest_signatures"]["comment"]
        self.assertEqual(initial["ids_modified_sha256"], final["ids_modified_sha256"])
        self.assertNotEqual(initial["objects_sha256"], final["objects_sha256"])
        self.assertEqual(initial["acquired_count"], final["acquired_count"])
        self.assertIn("comment", result["coverage"]["changed_endpoints"])
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])

    def test_inventory_retains_resolution_proof(self):
        evidence = {"host": "lessons.example", "mode": "synthetic_proxy_route_with_public_dns_corroboration",
                    "resolver": "https://dns.google/resolve", "proofs": [{"public_addresses": ["8.8.8.8"]}]}
        with patch("graphrag_core.lessons_corpus.acquire._resolve_public_host", return_value=evidence):
            result = self.capture()
        self.assertEqual(result["inventory"]["host_resolution_evidence"]["lessons.example"], evidence)

    def test_duplicate_ids_and_missing_pagination_do_not_claim_complete(self):
        self.network.duplicate_id = True
        result = self.capture()
        self.assertFalse(result["coverage"]["initial_rest_signatures"]["post"]["complete"])
        other = Path(self.temp.name) / "missing-headers"
        self.network.bad_pagination = True
        result = capture_wordpress(BASE + "/", other, rights_basis="Permission")
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])

    def test_rest_only_still_archives_pdf_and_declares_html_omission(self):
        result = self.capture(include_public_html=False)
        self.assertIn(BASE + "/source.pdf", result["inventory"]["pdf_acquired"])
        self.assertFalse(any(r["purpose"] == "public_html" for r in self.rows("acquisitions.jsonl")))
        self.assertTrue(result["coverage"]["pending_html_urls"])

    def test_dtd_sitemap_and_wrong_pdf_are_explicit_errors(self):
        self.network.bad_xml = True
        self.network.wrong_pdf = True
        result = self.capture()
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])
        self.assertTrue(any("DTD" in row.get("error", "") for row in result["coverage"]["errors"]))
        self.assertIn(BASE + "/source.pdf", result["coverage"]["pending_pdf_urls"])

    def test_unchecked_transport_redirect_is_refused(self):
        self.network.redirect_outside = True
        result = self.capture()
        self.assertTrue(any("outside" in row["reason"] for row in result["coverage"]["refusals"]))
        self.assertFalse(result["coverage"]["complete_within_declared_scope"])
        self.assertIn(BASE + "/orphan/", result["coverage"]["pending_html_urls"])
        rows = self.rows("acquisitions.jsonl")
        blocked = next(row for row in rows if row["requested_url"] == BASE + "/orphan/")
        self.assertIsNone(blocked["raw_path"])

    def test_fresh_destination_required_and_symlink_parent_refused(self):
        self.capture()
        before = (self.output / "inventory.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual((self.output / "inventory.json").read_bytes(), before)
        link = Path(self.temp.name) / "parent-link"
        link.symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaises(ValueError):
            capture_wordpress(BASE + "/", link / "unsafe", rights_basis="Permission")

    def test_url_policy_blocks_credentials_admin_traversal_private_and_external(self):
        policy = _URLPolicy(BASE + "/")
        bad = ["http://lessons.example/", "https://user:secret@lessons.example/",
               BASE + "/wp-admin/edit.php", BASE + "/%77p-admin/", BASE + "/%2e%2e/private",
               BASE + "/%252e%252e/private", BASE + "/x\\y", BASE + "/bad path",
               "https://external.example/", BASE + ":444/", "https://[bad/",
               "https://127.0.0.1/", "https://192.168.1.2/"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(AcquisitionRefused):
                policy.validate(url)
        self.assertEqual(policy.validate("https://www.lessons.example/a/#part"), "https://www.lessons.example/a/")

    def test_redirect_handler_only_allows_same_host_https_alias(self):
        handler = _SafeRedirect(_URLPolicy(BASE + "/"))
        request = urllib.request.Request(BASE + "/old/")
        for target in ["https://external.example/", "http://lessons.example/new/", BASE + "/wp-login.php"]:
            with self.assertRaises(AcquisitionRefused):
                handler.redirect_request(request, None, 302, "Redirect", {}, target)
        redirected = handler.redirect_request(request, None, 302, "Redirect", {}, "https://www.lessons.example/new/")
        self.assertEqual(redirected.full_url, "https://www.lessons.example/new/")

    def test_public_dns_required_before_destination_creation(self):
        self.resolver.stop()
        with patch("graphrag_core.lessons_corpus.acquire.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]):
            with self.assertRaises(AcquisitionRefused):
                self.capture()
        self.assertFalse(self.output.exists())

    def test_rights_and_positive_bounds_required(self):
        with self.assertRaises(ValueError):
            capture_wordpress(BASE + "/", self.output, rights_basis=" ")
        for kwargs in [{"max_urls": 0}, {"max_total_bytes": 0}]:
            with self.assertRaises(ValueError):
                self.capture(**kwargs)

    def test_document_kind_and_wordpress_id_contract(self):
        for kind in ("arbitrary", "public_html", "wordpress_metadata", "POST", "", None, [], {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                _document(kind, {"id": 1}, "acquisition:000001")
        for obj_id in (0, -1, True, False, "123", "post:123", 1.0, None, [], {}):
            with self.subTest(obj_id=obj_id), self.assertRaises(ValueError):
                _document("post", {"id": obj_id}, "acquisition:000001")
        with self.assertRaises(ValueError):
            _document("post", {}, "acquisition:000001")
        for kind in ("post", "page", "comment", "media", "category", "tag", "author", "series"):
            with self.subTest(kind=kind):
                row = _document(kind, {"id": 123}, "acquisition:000001")
                self.assertEqual(row["id"], kind + ":123")
                self.assertEqual(row["kind"], kind)
                self.assertEqual(row["metadata"]["id"], 123)


class DNSCorroborationTests(unittest.TestCase):
    def setUp(self):
        _SYNTHETIC_DNS_CACHE.clear()
        self.addCleanup(_SYNTHETIC_DNS_CACHE.clear)

    @staticmethod
    def records(*addresses):
        return [(2 if ":" not in address else 30, 1, 6, "", (address, 443)) for address in addresses]

    @staticmethod
    def proof(host, record_type):
        return {"question_name": host, "question_type": record_type, "status": 0,
                "public_addresses": ["8.8.8.8"] if record_type == 1 else [], "body_sha256": "proof"}

    def test_synthetic_v4_and_translated_v6_require_public_proof_and_cache(self):
        with patch("graphrag_core.lessons_corpus.acquire.socket.getaddrinfo", return_value=self.records(
            "198.18.0.186", "::ffff:0:c612:ba",
        )), patch("graphrag_core.lessons_corpus.acquire._lookup_public_dns", side_effect=self.proof) as lookup:
            policy = _URLPolicy(BASE + "/")
            policy.validate(BASE + "/another/")
            evidence = policy.resolution_evidence["lessons.example"]
            self.assertEqual(evidence["mode"], "synthetic_proxy_route_with_public_dns_corroboration")
            self.assertEqual(lookup.call_count, 2)
            self.assertEqual(evidence["cache_max_seconds"], 60)
            key = next(iter(_SYNTHETIC_DNS_CACHE))
            old, proof = _SYNTHETIC_DNS_CACHE[key]
            _SYNTHETIC_DNS_CACHE[key] = (old - _DNS_CACHE_SECONDS, proof)
            policy.validate(BASE + "/after-expiry/")
            self.assertEqual(lookup.call_count, 4)
            with self.assertRaises(AcquisitionRefused):
                policy.validate("https://other.example/outside/")
            self.assertEqual(lookup.call_count, 4)

    def test_ordinary_public_dns_never_uses_proxy_corroboration(self):
        with patch("graphrag_core.lessons_corpus.acquire.socket.getaddrinfo", return_value=self.records("8.8.8.8")), patch(
            "graphrag_core.lessons_corpus.acquire._lookup_public_dns",
        ) as lookup:
            evidence = _resolve_public_host("lessons.example")
            self.assertEqual(evidence["mode"], "public_system_dns")
            lookup.assert_not_called()

    def test_non_synthetic_private_addresses_never_use_dns_corroboration(self):
        bad = ["127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.1.1",
               "203.0.113.1", "198.51.100.1", "::1", "fc00::1", "::ffff:0:a00:1"]
        for address in bad:
            with self.subTest(address=address), patch(
                "graphrag_core.lessons_corpus.acquire.socket.getaddrinfo", return_value=self.records(address),
            ), patch("graphrag_core.lessons_corpus.acquire._lookup_public_dns") as lookup:
                with self.assertRaises(AcquisitionRefused):
                    _resolve_public_host("lessons.example")
                lookup.assert_not_called()

    def test_mixed_synthetic_and_private_address_is_refused(self):
        with patch("graphrag_core.lessons_corpus.acquire.socket.getaddrinfo", return_value=self.records(
            "198.18.0.186", "10.0.0.1",
        )), patch("graphrag_core.lessons_corpus.acquire._lookup_public_dns") as lookup:
            with self.assertRaises(AcquisitionRefused):
                _resolve_public_host("lessons.example")
            lookup.assert_not_called()

    def test_synthetic_route_without_public_answers_or_failed_resolver_is_refused(self):
        with patch("graphrag_core.lessons_corpus.acquire.socket.getaddrinfo", return_value=self.records("198.18.0.186")):
            with patch("graphrag_core.lessons_corpus.acquire._lookup_public_dns", return_value={"public_addresses": []}):
                with self.assertRaises(AcquisitionRefused):
                    _resolve_public_host("lessons.example")
            with patch("graphrag_core.lessons_corpus.acquire._lookup_public_dns", side_effect=AcquisitionRefused("failed")):
                with self.assertRaises(AcquisitionRefused):
                    _resolve_public_host("lessons.example")
        self.assertFalse(_SYNTHETIC_DNS_CACHE)

    def lookup(self, payload, *, status=200, final_url=None, content_type="application/json", raw=None):
        url = "https://dns.google/resolve?name=lessons.example&type=1"

        class Reply(io.BytesIO):
            def __init__(self):
                super().__init__(raw if raw is not None else json.dumps(payload).encode())
                self.status = status
                self.headers = {"Content-Type": content_type}

            def geturl(self):
                return final_url or url

        with patch("graphrag_core.lessons_corpus.acquire.urllib.request.build_opener") as build:
            build.return_value.open.return_value = Reply()
            result = _lookup_public_dns("lessons.example", 1)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, url)
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 8)
            self.assertTrue(any(isinstance(h, _DoHNoRedirect) for h in build.call_args.args))
            self.assertFalse(request.has_header("Authorization"))
            return result

    @staticmethod
    def valid_payload():
        return {"Status": 0, "Question": [{"name": "lessons.example.", "type": 1}],
                "Answer": [{"name": "lessons.example.", "type": 1, "data": "8.8.8.8"}]}

    def test_https_dns_question_and_direct_public_answer_identity(self):
        result = self.lookup(self.valid_payload())
        self.assertEqual(result["public_addresses"], ["8.8.8.8"])
        self.assertEqual(result["question_name"], "lessons.example")
        self.assertEqual(len(result["body_sha256"]), 64)

    def test_https_dns_rejects_schema_status_identity_private_answers_and_oversize(self):
        invalid = [[], {"Status": 3}, {"Status": True}, {"Status": 0, "Question": []}]
        for field, value in [("name", "other.example"), ("name", 42), ("type", 28), ("type", True)]:
            payload = self.valid_payload()
            payload["Question"][0][field] = value
            invalid.append(payload)
        for field, value in [("name", "other.example"), ("type", 5), ("type", 28),
                             ("data", "10.0.0.1"), ("data", "198.18.0.1"), ("data", "not-an-IP")]:
            payload = self.valid_payload()
            payload["Answer"][0][field] = value
            invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(AcquisitionRefused):
                self.lookup(payload)
        for kwargs in [{"status": 302}, {"final_url": "https://evil.example/"},
                       {"content_type": "text/html"}, {"raw": b"x" * (_DOH_MAX_BYTES + 1)},
                       {"raw": b"not-json"}]:
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(AcquisitionRefused):
                self.lookup(self.valid_payload(), **kwargs)

    def test_dns_resolver_redirect_is_never_followed(self):
        handler = _DoHNoRedirect()
        with self.assertRaises(AcquisitionRefused):
            handler.redirect_request(urllib.request.Request("https://dns.google/resolve"), None,
                                     302, "Redirect", {}, "https://other.example/")


if __name__ == "__main__":
    unittest.main()
