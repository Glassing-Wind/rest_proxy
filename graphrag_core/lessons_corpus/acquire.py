"""Bounded, provenance-preserving acquisition of a public WordPress corpus.

The permission supplied here authorizes archival acquisition only. It does not
assign a training or redistribution license. Remote text is data, never code.
"""

from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_REDIRECTS = 5
_DOH_MAX_BYTES = 1024 * 1024
_DNS_CACHE_SECONDS = 60.0
_SYNTHETIC_V4 = ipaddress.ip_network("198.18.0.0/15")
_TRANSLATED_V6 = ipaddress.ip_network("::ffff:0:0:0/96")
_SYNTHETIC_DNS_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, dict]] = {}
_ENDPOINTS = {
    "post": "posts", "page": "pages", "comment": "comments", "media": "media",
    "category": "categories", "tag": "tags", "author": "users",
}
_DOCUMENT_KINDS = frozenset(_ENDPOINTS) | {"series"}
_PAGINATED = re.compile(r"/(?:page/)?(?:[2-9]|[1-9][0-9]+)/?$")


class AcquisitionRefused(ValueError):
    """The requested URL or resource exceeds the declared acquisition scope."""


class AcquisitionBound(AcquisitionRefused):
    """An explicit acquisition bound was reached; completeness is not claimed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def _is_synthetic_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv4Address):
        return address in _SYNTHETIC_V4
    return address in _TRANSLATED_V6 and ipaddress.IPv4Address(int(address) & 0xFFFFFFFF) in _SYNTHETIC_V4


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address in _TRANSLATED_V6:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF).is_global
    return address.is_global


class _DoHNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AcquisitionRefused("The fixed HTTPS DNS resolver must not redirect")


def _lookup_public_dns(host: str, record_type: int) -> dict:
    """Verify one question with a fixed, certificate-validated HTTPS resolver."""
    if record_type not in (1, 28):
        raise AcquisitionRefused("Unsupported DNS record type")
    url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": host, "type": record_type})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _DoHNoRedirect())
    request = urllib.request.Request(url, headers={
        "Accept": "application/dns-json", "Accept-Encoding": "identity",
        "User-Agent": "Rest-proxy-lessons-archive/1.0",
    })
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200 or response.geturl() != url:
                raise AcquisitionRefused("The fixed HTTPS DNS resolver returned an unexpected response")
            media = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            if media not in ("application/json", "application/dns-json"):
                raise AcquisitionRefused("The HTTPS DNS resolver did not return JSON")
            body = response.read(_DOH_MAX_BYTES + 1)
            if len(body) > _DOH_MAX_BYTES:
                raise AcquisitionRefused("HTTPS DNS response exceeds its byte bound")
        data = json.loads(body)
    except AcquisitionRefused:
        raise
    except (OSError, ValueError, RecursionError, urllib.error.URLError) as exc:
        raise AcquisitionRefused("Public DNS corroboration failed") from exc
    if not isinstance(data, dict) or type(data.get("Status")) is not int or data["Status"] != 0:
        raise AcquisitionRefused("HTTPS DNS resolver returned a non-success DNS status")
    questions = data.get("Question")
    if not isinstance(questions, list) or len(questions) != 1:
        raise AcquisitionRefused("HTTPS DNS question identity is missing")
    question = questions[0]
    if (not isinstance(question, dict) or not isinstance(question.get("name"), str)
            or question["name"].lower().rstrip(".") != host):
        raise AcquisitionRefused("HTTPS DNS question name does not match the allowed host")
    if type(question.get("type")) is not int or question["type"] != record_type:
        raise AcquisitionRefused("HTTPS DNS question type does not match")
    answers = data.get("Answer", [])
    if not isinstance(answers, list):
        raise AcquisitionRefused("HTTPS DNS answer schema is invalid")
    addresses = []
    for answer in answers:
        if not isinstance(answer, dict) or not isinstance(answer.get("name"), str):
            raise AcquisitionRefused("HTTPS DNS answer schema is invalid")
        if answer["name"].lower().rstrip(".") != host:
            raise AcquisitionRefused("HTTPS DNS answer name does not match the allowed host")
        if type(answer.get("type")) is not int:
            raise AcquisitionRefused("HTTPS DNS answer type is invalid")
        if answer["type"] not in (1, 28):
            # CNAME chains are deliberately excluded: only direct, matching
            # public-address answers can corroborate the observed proxy route.
            raise AcquisitionRefused("HTTPS DNS answer is not a direct address record")
        if answer["type"] != record_type or not isinstance(answer.get("data"), str):
            raise AcquisitionRefused("HTTPS DNS answer type or address is invalid")
        try:
            address = ipaddress.ip_address(answer["data"])
        except ValueError as exc:
            raise AcquisitionRefused("HTTPS DNS answer address is invalid") from exc
        if not _is_public_address(address) or (address.version == 4) != (record_type == 1):
            raise AcquisitionRefused("HTTPS DNS answer is not a matching public address")
        addresses.append(str(address))
    return {"question_name": host, "question_type": record_type, "status": 0,
            "public_addresses": sorted(set(addresses)), "body_sha256": hashlib.sha256(body).hexdigest()}


def _resolve_public_host(host: str) -> dict:
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = sorted(set(ipaddress.ip_address(row[4][0]) for row in records), key=str)
    except (OSError, ValueError) as exc:
        raise AcquisitionRefused("Host cannot be resolved") from exc
    if not addresses or any(not _is_public_address(address) and not _is_synthetic_address(address) for address in addresses):
        raise AcquisitionRefused("Host resolves to a non-public address")
    system_addresses = tuple(str(address) for address in addresses)
    if not any(_is_synthetic_address(address) for address in addresses):
        return {"mode": "public_system_dns", "host": host, "system_addresses": list(system_addresses),
                "verified_at": _utc_now()}
    cache_key = (host, system_addresses)
    cached = _SYNTHETIC_DNS_CACHE.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _DNS_CACHE_SECONDS:
        return cached[1]
    proofs = [_lookup_public_dns(host, record_type) for record_type in (1, 28)]
    if not any(proof["public_addresses"] for proof in proofs):
        raise AcquisitionRefused("The synthetic proxy route has no corroborated public destination")
    evidence = {"mode": "synthetic_proxy_route_with_public_dns_corroboration", "host": host,
                "system_addresses": list(system_addresses), "verified_at": _utc_now(),
                "resolver": "https://dns.google/resolve", "proofs": proofs,
                "cache_max_seconds": _DNS_CACHE_SECONDS}
    _SYNTHETIC_DNS_CACHE[cache_key] = (time.monotonic(), evidence)
    return evidence


class _URLPolicy:
    def __init__(self, seed_url: str):
        seed = urllib.parse.urlsplit(seed_url)
        host = (seed.hostname or "").lower()
        self.hosts = {host, host[4:] if host.startswith("www.") else "www." + host}
        self.resolution_evidence: dict[str, dict] = {}
        self.seed_url = self.validate(seed_url)
        parsed = urllib.parse.urlsplit(self.seed_url)
        self.origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    def validate(self, url: str) -> str:
        if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url) or "\\" in url:
            raise AcquisitionRefused("Malformed URL")
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise AcquisitionRefused("Malformed URL") from exc
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise AcquisitionRefused("Only credential-free HTTPS URLs are accepted")
        if port not in (None, 443) or host not in self.hosts:
            raise AcquisitionRefused("URL is outside the public seed host")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or ".." in host:
            raise AcquisitionRefused("Malformed hostname")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise AcquisitionRefused("Private hosts are not accepted")
        path = parsed.path or "/"
        decoded = path
        for _ in range(4):
            decoded_next = urllib.parse.unquote(decoded)
            if decoded_next == decoded:
                break
            decoded = decoded_next
        pieces = decoded.lower().split("/")
        if "\\" in decoded or any(ord(c) < 32 for c in decoded) or any(p in (".", "..") for p in pieces):
            raise AcquisitionRefused("Unsafe URL path")
        if any(p in ("wp-admin", "wp-login.php", "xmlrpc.php") for p in pieces):
            raise AcquisitionRefused("Administrative and login endpoints are excluded")
        resolution = _resolve_public_host(host)
        if isinstance(resolution, dict):
            self.resolution_evidence[host] = resolution
        return urllib.parse.urlunsplit(("https", parsed.netloc.lower(), path, parsed.query, ""))


@dataclass
class _Response:
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy: _URLPolicy):
        super().__init__()
        self.policy = policy
        self.max_redirections = _MAX_REDIRECTS
        self.max_repeats = _MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked = self.policy.validate(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, checked)


class _Transport:
    def __init__(self, policy: _URLPolicy):
        # Disable implicit environment proxies; only the checked public origin
        # is contacted. HTTPS uses the platform's normal certificate validation.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SafeRedirect(policy))
        self.policy = policy
        self.last_request = 0.0

    def fetch(self, url: str, limit: int) -> _Response:
        checked = self.policy.validate(url)
        remaining = 0.1 - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request = time.monotonic()
        request = urllib.request.Request(checked, headers={
            "User-Agent": "Rest-proxy-lessons-archive/1.0",
            "Accept-Encoding": "identity",
        })
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            final = self.policy.validate(response.geturl())
            body = response.read(limit + 1)
            if len(body) > limit:
                raise AcquisitionBound("Response exceeds the remaining response/total byte bound")
            return _Response(final, response.status, {k.lower(): v for k, v in response.headers.items()}, body)


class _Links(HTMLParser):
    def __init__(self, url: str):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = {"a": "href", "iframe": "src", "embed": "src", "object": "data"}.get(tag)
        value = values.get(attribute) if attribute else None
        if value:
            try:
                self.links.append(urllib.parse.urljoin(self.url, value))
                parsed = urllib.parse.urlsplit(value)
            except ValueError:
                self.links.append(value)
                return
            embedded = urllib.parse.parse_qs(parsed.query).get("pdfemb-data", [])
            for item in embedded:
                if len(item) > 65536:
                    continue
                try:
                    payload = json.loads(base64.b64decode(item, validate=True))
                    if isinstance(payload.get("url"), str):
                        self.links.append(payload["url"])
                except (ValueError, KeyError, TypeError):
                    continue


def _rendered(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("rendered", ""))
    return str(value or "")


def _document(kind: str, obj: dict, acquisition_id: str) -> dict:
    if not isinstance(kind, str) or kind not in _DOCUMENT_KINDS:
        raise ValueError("Unsupported WordPress document kind")
    if not isinstance(obj, dict) or type(obj.get("id")) is not int or obj["id"] <= 0:
        raise ValueError("Require a positive integer WordPress object ID")
    return {
        "id": f"{kind}:{obj['id']}", "kind": kind,
        "url": obj.get("link", ""),
        "title_html": _rendered(obj.get("title")) or html.escape(str(obj.get("name", ""))),
        "content_html": _rendered(obj.get("content")) or _rendered(obj.get("description")),
        "excerpt_html": _rendered(obj.get("excerpt")),
        "author": obj.get("author", obj.get("author_name")),
        "published_gmt": obj.get("date_gmt"), "modified_gmt": obj.get("modified_gmt"),
        "raw_acquisition_id": acquisition_id, "metadata": obj,
    }


class _Capture:
    def __init__(self, policy: _URLPolicy, output: Path, max_urls: int, max_bytes: int):
        self.policy = policy
        self.output = output
        self.max_urls = max_urls
        self.max_bytes = max_bytes
        self.transport = _Transport(policy)
        self.acquisitions: list[dict] = []
        self.documents: list[dict] = []
        self.requested: set[str] = set()
        self.bytes = 0
        self.errors: list[dict] = []
        self.refusals: list[dict] = []
        self.exhausted = False
        self.rest_counts: dict[str, int] = {}
        self.signatures: dict[str, dict] = {}
        self.sitemap_urls: set[str] = set()
        self.html_targets: set[str] = {policy.seed_url, policy.origin + "/"}
        self.pdf_targets: set[str] = set()
        self.html_attempted: set[str] = set()
        self.html_acquired: set[str] = set()
        self.pdf_acquired: set[str] = set()

    def refuse(self, url: str, reason: str) -> None:
        self.refusals.append({"url": url, "reason": reason})

    def fetch(self, url: str, *, purpose: str) -> tuple[_Response | None, dict]:
        row = {"id": f"acquisition:{len(self.acquisitions) + 1:06d}", "purpose": purpose,
               "requested_url": url, "final_url": None, "status": None,
               "media_type": None, "encoding": None, "body_sha256": None,
               "raw_path": None, "captured_at": _utc_now()}
        self.acquisitions.append(row)
        try:
            checked = self.policy.validate(url)
            if checked not in self.requested and len(self.requested) >= self.max_urls:
                raise AcquisitionBound("Maximum unique requested URLs reached")
            if self.bytes >= self.max_bytes:
                raise AcquisitionBound("Maximum total response bytes reached")
            self.requested.add(checked)
            response = self.transport.fetch(checked, min(_MAX_RESPONSE_BYTES, self.max_bytes - self.bytes))
            self.policy.validate(response.final_url)
            if len(response.body) > min(_MAX_RESPONSE_BYTES, self.max_bytes - self.bytes):
                raise AcquisitionBound("Transport response exceeds the byte bound")
            content_type = response.headers.get("content-type", "application/octet-stream")
            charset = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", content_type, re.I)
            digest = hashlib.sha256(response.body).hexdigest()
            raw_path = f"raw/{digest}.bin"
            path = self.output / raw_path
            if not path.exists():
                _write_private(path, response.body)
            self.bytes += len(response.body)
            row.update(final_url=response.final_url, status=response.status,
                       media_type=content_type.split(";", 1)[0].strip().lower(),
                       encoding=charset.group(1) if charset else "utf-8",
                       body_sha256=digest, raw_path=raw_path,
                       headers={k: v for k, v in response.headers.items()
                                if k in ("x-wp-total", "x-wp-totalpages", "etag", "last-modified", "content-type")})
            if response.status < 200 or response.status >= 300:
                self.errors.append({"url": url, "status": response.status, "purpose": purpose})
            return response, row
        except AcquisitionRefused as exc:
            row["error"] = str(exc)
            self.refuse(url, str(exc))
            if isinstance(exc, AcquisitionBound):
                self.exhausted = True
        except (OSError, ValueError, urllib.error.URLError) as exc:
            # URLs cannot contain credentials under the policy. Do not retain
            # exception payloads from third-party servers in diagnostic text.
            row["error"] = f"Acquisition failed: {type(exc).__name__}"
            self.errors.append({"url": url, "error": row["error"], "purpose": purpose})
        return None, row

    def text(self, response: _Response, row: dict) -> str | None:
        try:
            return response.body.decode(row["encoding"] or "utf-8")
        except (UnicodeError, LookupError):
            self.errors.append({"url": row["requested_url"], "error": "Text decoding failed; raw bytes retained"})
            return None

    def json(self, url: str, purpose: str) -> tuple[Any | None, dict]:
        response, row = self.fetch(url, purpose=purpose)
        if response is None or not 200 <= response.status < 300:
            return None, row
        try:
            return json.loads(response.body), row
        except (ValueError, UnicodeError):
            self.errors.append({"url": url, "error": "Invalid JSON response", "purpose": purpose})
            return None, row

    def rest(self, kind: str, endpoint: str, *, drift: bool = False) -> dict:
        objects: list[dict] = []
        reported_total = None
        page, pages = 1, 1
        complete = True
        while page <= pages and not self.exhausted:
            url = self.policy.origin + f"/wp-json/wp/v2/{endpoint}?per_page=100&page={page}"
            data, row = self.json(url, "drift:" + kind if drift else "rest:" + kind)
            if not isinstance(data, list) or any(not isinstance(x, dict) or "id" not in x for x in data):
                complete = False
                break
            try:
                headers = row.get("headers", {})
                total = int(headers["x-wp-total"])
                page_count = int(headers["x-wp-totalpages"])
                if total < 0 or page_count < 0 or page_count > self.max_urls:
                    raise ValueError
                if reported_total is not None and total != reported_total:
                    complete = False
                reported_total = total
                pages = max(1, page_count)
            except (KeyError, ValueError, TypeError):
                self.errors.append({"url": url, "error": "Missing/invalid WordPress pagination headers"})
                complete = False
                break
            objects.extend(data)
            if not drift:
                self.documents.extend(_document(kind, obj, row["id"]) for obj in data)
            page += 1
        ids = [str(obj["id"]) for obj in objects]
        if self.exhausted or len(ids) != len(set(ids)) or reported_total != len(objects):
            complete = False
        signature_input = sorted(
            ((str(o["id"]), o.get("modified_gmt"), o.get("date_gmt")) for o in objects),
            key=lambda item: item[0],
        )
        signature = {"reported_total": reported_total, "acquired_count": len(objects),
                     "ids_modified_sha256": hashlib.sha256(_json_bytes(signature_input)).hexdigest(),
                     # No object fields are excluded: comments, bios, media,
                     # and terms can change without a modification timestamp.
                     "objects_sha256": hashlib.sha256(_json_bytes(
                         sorted(objects, key=lambda obj: str(obj["id"])),
                     )).hexdigest(),
                     "complete": complete}
        if not drift:
            self.rest_counts[kind] = len(objects)
            self.signatures[kind] = signature
        if not complete:
            self.errors.append({"endpoint": endpoint, "error": "REST pagination incomplete or inconsistent",
                                "phase": "drift" if drift else "initial"})
        return signature

    def add_link(self, value: str, base: str, *, html_link: bool = False) -> None:
        try:
            url = urllib.parse.urljoin(base, value)
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            self.refuse(value, "Malformed linked URL")
            return
        if (parsed.hostname or "").lower() not in self.policy.hosts:
            return
        try:
            checked = self.policy.validate(url)
        except AcquisitionRefused as exc:
            self.refuse(url, str(exc))
            return
        path = urllib.parse.urlsplit(checked).path
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            self.pdf_targets.add(checked)
        elif suffix in ("", ".html", ".htm") and (not html_link or _PAGINATED.search(path)) and not parsed.query:
            self.html_targets.add(checked)

    def discover_document_links(self) -> None:
        for doc in self.documents:
            if doc["kind"] in ("post", "page", "comment", "category", "tag", "author", "series"):
                self.add_link(doc["url"], self.policy.origin)
            if doc["kind"] in ("post", "page"):
                breaks = len(re.findall(r"<!--\s*nextpage\s*-->", doc["content_html"], re.I))
                if breaks >= self.max_urls:
                    self.refuse(doc["url"], "Numbered content pages exceed the URL bound")
                    self.exhausted = True
                else:
                    for page in range(2, breaks + 2):
                        self.add_link(doc["url"].rstrip("/") + f"/{page}/", self.policy.origin)
            text_fields = [doc["content_html"], doc["excerpt_html"]]
            if doc["kind"] == "media":
                text_fields.append(_rendered(doc["metadata"].get("caption")))
            for text in text_fields:
                parser = _Links(doc["url"] or self.policy.origin)
                parser.feed(text)
                for value in parser.links:
                    self.add_link(value, parser.url)
            if doc["kind"] == "media":
                self.add_link(str(doc["metadata"].get("source_url", "")), self.policy.origin)

    def sitemap(self, url: str, seen: set[str], depth: int = 0) -> None:
        if url in seen or self.exhausted:
            return
        if depth > 5:
            self.refuse(url, "Sitemap nesting exceeds five levels")
            return
        seen.add(url)
        response, row = self.fetch(url, purpose="sitemap")
        if response is None or not 200 <= response.status < 300:
            return
        if b"<!doctype" in response.body.lower() or b"<!entity" in response.body.lower():
            self.errors.append({"url": url, "error": "DTD/entity-bearing XML is excluded"})
            return
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError:
            self.errors.append({"url": url, "error": "Invalid sitemap XML"})
            return
        local = root.tag.rsplit("}", 1)[-1]
        if local not in ("sitemapindex", "urlset"):
            self.errors.append({"url": url, "error": "Unexpected sitemap root"})
            return
        for child in root:
            loc = next((item.text for item in child if item.tag.rsplit("}", 1)[-1] == "loc"), None)
            if not loc:
                continue
            if local == "sitemapindex":
                try:
                    checked = self.policy.validate(loc)
                except AcquisitionRefused as exc:
                    self.refuse(loc, str(exc))
                    continue
                self.sitemap(checked, seen, depth + 1)
            else:
                self.sitemap_urls.add(loc)
                self.add_link(loc, self.policy.origin)

    def public_html(self) -> None:
        while self.html_targets - self.html_attempted and not self.exhausted:
            url = min(self.html_targets - self.html_attempted)
            self.html_attempted.add(url)
            response, row = self.fetch(url, purpose="public_html")
            if response is None or not 200 <= response.status < 300:
                continue
            if row["media_type"] not in ("text/html", "application/xhtml+xml"):
                self.errors.append({"url": url, "error": "Expected public HTML"})
                continue
            text = self.text(response, row)
            if text is not None:
                self.html_acquired.add(url)
                parser = _Links(response.final_url)
                parser.feed(text)
                for link in parser.links:
                    self.add_link(link, parser.url, html_link=True)

    def pdfs(self) -> None:
        for url in sorted(self.pdf_targets):
            if self.exhausted:
                break
            response, _ = self.fetch(url, purpose="referenced_pdf")
            if response is not None and 200 <= response.status < 300:
                if not response.body.startswith(b"%PDF-"):
                    self.errors.append({"url": url, "error": "Referenced PDF lacks PDF magic bytes"})
                else:
                    self.pdf_acquired.add(url)


def capture_wordpress(
    seed_url: str,
    output_dir: Path,
    *,
    rights_basis: str,
    max_urls: int = 1200,
    include_public_html: bool = True,
    max_total_bytes: int = 268435456,
) -> dict:
    """Archive a bounded current public corpus into a fresh owner-only directory.

    Raw responses are immutable content-addressed bodies. Extraction, learning,
    public redistribution, external-reference crawling, and model calls are not
    performed here. Every acquired or failed request has a manifest row.
    """
    if not rights_basis or not rights_basis.strip():
        raise ValueError("An archival rights basis is required")
    if max_urls < 1 or max_total_bytes < 1:
        raise ValueError("Acquisition bounds must be positive")
    policy = _URLPolicy(seed_url)
    output = Path(output_dir).expanduser()
    if output.exists() or output.is_symlink():
        raise FileExistsError("The acquisition directory must be fresh")
    if not output.parent.is_dir() or output.parent.resolve() != output.parent.absolute():
        raise ValueError("Use an existing real parent directory without symlink components")
    output.mkdir(mode=0o700)
    (output / "raw").mkdir(mode=0o700)
    capture = _Capture(policy, output, max_urls, max_total_bytes)
    endpoints = dict(_ENDPOINTS)
    final_signatures: dict[str, dict] = {}
    drift: list[str] = []
    try:
        taxonomies, _ = capture.json(policy.origin + "/wp-json/wp/v2/taxonomies", "taxonomy_discovery")
        if isinstance(taxonomies, dict):
            for name, value in taxonomies.items():
                if name == "series" and isinstance(value, dict):
                    rest_base = value.get("rest_base", "series")
                    if isinstance(rest_base, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", rest_base):
                        endpoints["series"] = rest_base
        for kind, endpoint in endpoints.items():
            if capture.exhausted:
                break
            capture.rest(kind, endpoint)
        capture.discover_document_links()
        robots, row = capture.fetch(policy.origin + "/robots.txt", purpose="robots")
        sitemap_roots = {policy.origin + "/sitemap_index.xml"}
        if robots is not None and 200 <= robots.status < 300:
            text = capture.text(robots, row)
            if text:
                sitemap_roots.update(match.group(1).strip() for match in re.finditer(
                    r"^Sitemap:\s*(\S+)\s*$", text, re.I | re.M))
        seen: set[str] = set()
        for root in sorted(sitemap_roots):
            capture.sitemap(root, seen)
        if include_public_html:
            capture.public_html()
        # Same-origin PDFs remain original sources even in REST-only mode.
        capture.pdfs()
        for kind, endpoint in endpoints.items():
            if capture.exhausted:
                break
            final_signatures[kind] = capture.rest(kind, endpoint, drift=True)
        drift = [kind for kind in endpoints if capture.signatures.get(kind) != final_signatures.get(kind)]
    except Exception as exc:
        capture.errors.append({"error": f"Acquisition interrupted: {type(exc).__name__}"})
        drift = [kind for kind in endpoints if kind not in final_signatures]
    finally:
        _write_private(output / "acquisitions.jsonl", b"".join(_json_bytes(row) for row in capture.acquisitions))
        _write_private(output / "documents.jsonl", b"".join(_json_bytes(row) for row in capture.documents))
    inventory = {
        "schema_version": 1, "seed_url": policy.seed_url, "captured_at": _utc_now(),
        "rights_basis": rights_basis, "rights_scope": "user-attested archival permission only",
        "training_license": "not established", "redistribution_license": "not established",
        "document_counts": capture.rest_counts, "rest_endpoints": endpoints,
        "documents": len(capture.documents), "acquisitions": len(capture.acquisitions),
        "unique_requested_urls": len(capture.requested), "response_bytes": capture.bytes,
        "host_resolution_evidence": policy.resolution_evidence,
        "sitemap_urls": sorted(capture.sitemap_urls), "html_targets": sorted(capture.html_targets),
        "pdf_targets": sorted(capture.pdf_targets), "pdf_acquired": sorted(capture.pdf_acquired),
    }
    coverage = {
        "schema_version": 1,
        "complete_within_declared_scope": not capture.errors and not capture.exhausted and not drift
        and not any(row.get("error") for row in capture.acquisitions)
        and not (capture.pdf_targets - capture.pdf_acquired),
        "scope": "current public WordPress objects, sitemap inventory, same-origin referenced PDF originals"
        + (", public HTML" if include_public_html else ""),
        "include_public_html": include_public_html,
        "bounds": {"max_urls": max_urls, "max_total_bytes": max_total_bytes,
                   "max_response_bytes": _MAX_RESPONSE_BYTES, "max_redirects": _MAX_REDIRECTS},
        "bound_reached": capture.exhausted, "errors": capture.errors, "refusals": capture.refusals,
        "initial_rest_signatures": capture.signatures, "final_rest_signatures": final_signatures,
        "changed_endpoints": drift, "pending_html_urls": sorted(capture.html_targets - capture.html_acquired)
        if include_public_html else sorted(capture.html_targets),
        "pending_pdf_urls": sorted(capture.pdf_targets - capture.pdf_acquired),
        "omissions": ["private/deleted records and historical revisions", "external reference bodies",
                      "image/audio/video content and OCR", "PDF text extraction", "model training or inference"],
    }
    _write_private(output / "inventory.json", _json_bytes(inventory))
    _write_private(output / "coverage.json", _json_bytes(coverage))
    return {"output_dir": str(output), "inventory": inventory, "coverage": coverage}
