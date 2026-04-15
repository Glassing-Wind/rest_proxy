import inspect
import tempfile
import unittest
from pathlib import Path

import tree_sitter_language_pack as ts_pack


class TsPackContractTests(unittest.TestCase):
    def test_required_symbols_exist(self):
        required = [
            "ProcessConfig",
            "process",
            "detect_language",
            "detect_language_from_extension",
            "has_language",
            "download",
            "build_semantic_payload",
            "build_line_window_chunks",
            "build_swift_chunks",
            "execute_codebase_embedding_upsert",
            "execute_semantic_index_driver",
        ]

        for name in required:
            self.assertTrue(hasattr(ts_pack, name), f"missing tree_sitter_language_pack.{name}")

    def test_python_detection_and_process_contract(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample_path = Path(tmp_dir) / "sample.py"
            sample_path.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")

            self.assertEqual(ts_pack.detect_language_from_extension("py"), "python")
            self.assertEqual(ts_pack.detect_language(str(sample_path)), "python")

        config = ts_pack.ProcessConfig("python", chunk_max_size=256)
        result = ts_pack.process("def greet(name):\n    return f'hi {name}'\n", config)

        self.assertIsInstance(result, dict)
        self.assertIn("language", result)
        self.assertEqual(result["language"], "python")
        self.assertIn("structure", result)
        self.assertIsInstance(result["structure"], list)

    def test_semantic_payload_and_line_window_contract(self):
        source = "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha()\n"
        payload_kwargs = {
            "chunk_id_version": "v1",
            "chunk_max_size": 256,
        }
        payload_sig = inspect.signature(ts_pack.build_semantic_payload)
        if "chunk_overlap" in payload_sig.parameters:
            payload_kwargs["chunk_overlap"] = 32
        elif "_chunk_overlap" in payload_sig.parameters:
            payload_kwargs["_chunk_overlap"] = 32

        payload = ts_pack.build_semantic_payload(
            source,
            "python",
            "src/sample.py",
            "proj123",
            **payload_kwargs,
        )

        self.assertIsInstance(payload, dict)
        self.assertIn("chunks", payload)
        self.assertIn("file_meta", payload)
        self.assertIsInstance(payload["chunks"], list)
        self.assertGreaterEqual(len(payload["chunks"]), 1)
        required_fields = {
            "member_usages",
            "call_like_symbols",
            "declared_symbols",
            "contains_definition",
            "contains_entrypoint",
            "chunk_role",
        }
        self.assertTrue(required_fields.issubset((payload["chunks"][0].get("metadata") or {}).keys()))

        fallback_chunks = ts_pack.build_line_window_chunks(
            source,
            "src/sample.py",
            "proj123",
            language="python",
            file_meta=payload.get("file_meta") or {},
            chunk_id_version="v1",
            chunk_lines=20,
            overlap_lines=5,
        )
        self.assertIsInstance(fallback_chunks, list)
        self.assertGreaterEqual(len(fallback_chunks), 1)
        self.assertIn("metadata", fallback_chunks[0])
        self.assertTrue(required_fields.issubset((fallback_chunks[0].get("metadata") or {}).keys()))

    def test_swift_and_embedding_helpers_have_expected_parameters(self):
        swift_sig = inspect.signature(ts_pack.build_swift_chunks)
        self.assertTrue({"source", "file_path", "project_id"}.issubset(swift_sig.parameters))

        upsert_sig = inspect.signature(ts_pack.execute_codebase_embedding_upsert)
        self.assertTrue({"cursor", "batch", "project_id"}.issubset(upsert_sig.parameters))

        driver_sig = inspect.signature(ts_pack.execute_semantic_index_driver)
        self.assertTrue({"conn", "project_id", "manifest_paths", "all_chunks"}.issubset(driver_sig.parameters))


if __name__ == "__main__":
    unittest.main()
