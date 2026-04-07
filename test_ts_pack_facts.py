import unittest

from graphrag_core.ts_pack_facts import extract_file_facts, route_path_from_file


class FakeTsPackExtractOnly:
    def __init__(self, raw_result):
        self._raw_result = raw_result

    def extract(self, source, config):
        return self._raw_result


class FakeTsPackFileFacts:
    def __init__(self, facts):
        self._facts = facts

    def extract_file_facts(self, source, language, file_path):
        return self._facts


class TsPackFactsTests(unittest.TestCase):
    def test_route_path_from_file_handles_route_and_index_files(self):
        self.assertEqual(route_path_from_file("src/api/units/route.ts"), "/api/units")
        self.assertEqual(route_path_from_file("src/pages/api/users/index.ts"), "/api/users")
        self.assertEqual(
            route_path_from_file("apps/web/src/app/projects/[id]/route.ts"),
            "/projects/[id]",
        )

    def test_prefers_parser_owned_file_facts_when_available(self):
        facts = {
            "route_defs": [{"framework": "file_route", "method": "GET", "path": "/api/leases"}],
            "http_calls": [{"client": "fetch", "method": "POST", "path": "/api/units"}],
        }
        ts_pack = FakeTsPackFileFacts(facts)
        result = extract_file_facts(
            ts_pack,
            "export async function GET() {}",
            "typescript",
            "src/api/leases/route.ts",
        )
        self.assertEqual(result, facts)

    def test_fallback_extracts_routes_and_http_calls_precisely(self):
        ts_pack = FakeTsPackExtractOnly(
            {
                "results": {
                    "express_routes": {
                        "matches": [
                            {
                                "captures": [
                                    {"name": "method", "text": "post"},
                                    {"name": "path", "text": "/api/leases"},
                                ]
                            }
                        ]
                    },
                    "http_member_calls": {
                        "matches": [
                            {
                                "captures": [
                                    {"name": "client", "text": "router"},
                                    {"name": "method", "text": "post"},
                                    {"name": "path", "text": "/api/leases"},
                                ]
                            },
                            {
                                "captures": [
                                    {"name": "client", "text": "client"},
                                    {"name": "method", "text": "get"},
                                    {"name": "path", "text": "/api/properties"},
                                ]
                            },
                        ]
                    },
                    "http_fetch_calls": {
                        "matches": [
                            {
                                "captures": [
                                    {"name": "client", "text": "fetch"},
                                    {"name": "path", "text": "/api/units"},
                                ]
                            }
                        ]
                    },
                    "http_method_props": {
                        "matches": [
                            {
                                "captures": [
                                    {"name": "key", "text": "method"},
                                    {"name": "method", "text": "POST"},
                                ]
                            }
                        ]
                    },
                    "route_methods": {
                        "matches": [
                            {
                                "captures": [
                                    {"name": "method", "text": "GET"},
                                ]
                            }
                        ]
                    },
                }
            }
        )

        result = extract_file_facts(
            ts_pack,
            "fixture",
            "typescript",
            "src/api/leases/route.ts",
        )

        self.assertIn(
            {"framework": "express", "method": "POST", "path": "/api/leases"},
            result["route_defs"],
        )
        self.assertIn(
            {"framework": "file_route", "method": "GET", "path": "/api/leases"},
            result["route_defs"],
        )
        self.assertIn(
            {"client": "fetch", "method": "POST", "path": "/api/units"},
            result["http_calls"],
        )
        self.assertIn(
            {"client": "client", "method": "GET", "path": "/api/properties"},
            result["http_calls"],
        )
        self.assertNotIn(
            {"client": "router", "method": "POST", "path": "/api/leases"},
            result["http_calls"],
        )

    def test_fallback_extracts_swift_resource_refs(self):
        ts_pack = FakeTsPackExtractOnly(
            {
                "results": {
                    "resource_calls": {
                        "matches": [
                            {
                                "captures": [
                                    {"name": "callee", "text": "Image"},
                                    {"name": "name", "text": "hero"},
                                ]
                            },
                            {
                                "captures": [
                                    {"name": "callee", "text": "Color"},
                                    {"name": "name", "text": "brand"},
                                ]
                            },
                        ]
                    }
                }
            }
        )

        result = extract_file_facts(ts_pack, "fixture", "swift", "UI/View.swift")
        self.assertEqual(
            result["resource_refs"],
            [
                {"callee": "Color", "kind": "color", "name": "brand"},
                {"callee": "Image", "kind": "image", "name": "hero"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
