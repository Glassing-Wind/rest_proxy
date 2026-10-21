"""Disposable native publication/profile tests; no services, models or analyzer install.

The parser, Ladybug and LanceDB are real. Three-dimensional vectors and analyzer
responses are deterministic fixtures, so this suite makes no similarity/quality claim.
Run: .venv/bin/python test_repository_learning.py
"""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


async def fixture_embed(texts):
    return [[1.0, 0.0, 0.0] for _ in texts]


def declaration_names(value):
    """Observe structured declaration identifiers, never infer them from source text."""
    if isinstance(value, dict):
        names = {value["name"]} if isinstance(value.get("name"), str) else set()
        for nested in value.values():
            names.update(declaration_names(nested))
        return names
    if isinstance(value, list):
        return set().union(*(declaration_names(nested) for nested in value))
    return set()


class EchoAdapter:
    """Return provenance-bearing analysis fixtures; store detached worker inputs."""

    def __init__(self, runtime, *, status="configured", hook=None):
        self.runtime = runtime
        self.status = status
        self.hook = hook
        self.calls = []

    def availability(self):
        return {"status": self.status}

    @staticmethod
    def profile(payload):
        metadata = deepcopy(payload["metadata"])
        return {"repo_name": metadata["project_id"], "metadata": metadata,
                "patterns": [], "conventions": {"declarations": sorted(declaration_names(payload))}}

    async def analyze(self, operation, *, payload=None, reference_payload=None, target_payload=None):
        # Analysis may wait on external computation. It must not retain the
        # publication locks or prevent unrelated repository reads/publications.
        if self.runtime._lock.locked() or self.runtime._owner._lock.locked():
            raise AssertionError("Publication lock held while analyzer awaited")
        await self.runtime.overview("left")
        self.calls.append(deepcopy({"operation": operation, "payload": payload,
                                    "reference_payload": reference_payload, "target_payload": target_payload}))
        if self.hook is not None:
            return await self.hook(operation, payload, reference_payload, target_payload)
        result = (self.profile(payload) if operation == "profile" else
                  {"reference_profile": self.profile(reference_payload),
                   "target_profile": self.profile(target_payload)})
        return {"ok": True, "result": result, "backend": {"name": "RepoAnalyzer"}}


class RepositoryLearningTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        config_root = cls.resources.enter_context(tempfile.TemporaryDirectory(prefix="learning-config-"))
        cls.resources.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1",
            "LM_PROXY_STORAGE_BACKEND": "embedded",
            "LM_PROXY_GRAPH_BACKEND": "ladybug",
            "LM_PROXY_MEMORY_ENABLED": "0",
            "LM_PROXY_MEMORY_MODE": "off",
            "LM_PROXY_MEMORY_ENABLE_PERSISTENCE": "0",
            "LM_PROXY_MEMORY_ENABLE_REDIS": "0",
            "LM_PROXY_WATCHER_ENABLED": "0",
            "LM_PROXY_EMBEDDED_WATCH_ENABLED": "0",
            "LM_PROXY_RUNTIME_DIR": str(Path(config_root) / "runtime"),
            "LM_PROXY_CONFIG_DIR": str(Path(config_root) / "config"),
            "LM_PROXY_DEBUG": "false",
        }, clear=True))
        cls.snapshot_module = importlib.import_module("graphrag_core.repository_learning.snapshot")
        cls.service_module = importlib.import_module("graphrag_core.repository_learning.service")
        cls.runtime_module = importlib.import_module("memory.embedded_runtime")

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="repository-learning-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.runtime = self.runtime_module.EmbeddedRuntime(str(self.directory / "state"), 3)
        self.addAsyncCleanup(self.runtime.close)
        self.owner = await self.runtime._get_owner()
        self.sources = {
            "left": {
                "main.py": "# café retained source\nfrom helper import normalize\nclass Policy:\n"
                           "    def authorize(self, token):\n        return normalize(token)\n"
                           "def authenticate(token):\n    return Policy().authorize(token)\n",
                "helper.py": "def normalize(token):\n    return token.strip()\n",
            },
            "right": {
                "main.py": "class Policy:\n    def authorize(self, token):\n"
                           "        return token is not None\ndef authenticate(token):\n"
                           "    return Policy().authorize(token)\n",
                "helper.py": "def helper_only():\n    return 7\n",
            },
        }
        self.publications = {}
        for project, files in self.sources.items():
            root = self.directory / project
            root.mkdir()
            for path, source in files.items():
                (root / path).write_text(source, encoding="utf-8")
            (root / "unlisted.py").write_text("def secret_decoy():\n    return 'unlisted'\n")
            self.publications[project] = await self.owner.index(
                str(root), project, sorted(files), embed=fixture_embed, encoder_id="fixture-v1")
        # Published evidence operations must never obtain an embedding provider.
        self.encoder_guard = patch.object(self.runtime, "_get_encoder", AsyncMock(
            side_effect=AssertionError("Learning obtained an embedding provider")))
        self.encoder_guard.start()
        self.addCleanup(self.encoder_guard.stop)

    def gateway(self, adapter=None):
        adapter = adapter or EchoAdapter(self.runtime)
        return self.service_module.RepositoryLearningGateway(self.runtime, adapter), adapter

    def assert_source_receipts(self, sources, projects):
        expected = {(project, path) for project in projects for path in self.sources[project]}
        self.assertEqual({(source["project_id"], source["file_path"]) for source in sources}, expected)
        for source in sources:
            project, path = source["project_id"], source["file_path"]
            original = self.sources[project][path]
            self.assertEqual(source["source_sha256"], hashlib.sha256(original.encode()).hexdigest())
            self.assertEqual(source["run_id"], self.publications[project]["run_id"])
            manifest = next(file for file in self.publications[project]["manifest"]["files"]
                            if file["path"] == path)
            self.assertEqual(source["facts_sha256"], manifest["facts_sha256"])
            self.assertEqual(source["outline_sha256"], manifest["outline_sha256"])
            self.assertEqual(source["total_symbols"], len(source["symbols"]))
            for symbol in source["symbols"]:
                self.assertIn(symbol["name"], "\n".join(original.splitlines()[
                    symbol["start_line"] - 1:symbol["end_line"]]))

    async def test_native_declarations_snapshot_identity_detachment_and_reopen(self):
        first = await self.runtime.capture_learning_snapshots(["left", "right"])
        self.assert_source_receipts([source for snapshot in first for source in snapshot["sources"]],
                                    ["left", "right"])
        self.assertEqual([snapshot["citation"]["project_id"] for snapshot in first], ["left", "right"])
        for snapshot in first:
            project = snapshot["citation"]["project_id"]
            self.assertEqual(snapshot["citation"]["run_id"], self.publications[project]["run_id"])
            self.assertEqual(snapshot["citation"]["manifest_sha256"], self.publications[project]["manifest"]["sha256"])
            self.assertTrue(snapshot["coverage"]["complete"])
            self.assertEqual(snapshot["coverage"]["file_selection"], "path-order")
            self.assertTrue({"Policy", "authorize", "authenticate"}.issubset(
                declaration_names(snapshot["payload"])))
            self.assertNotIn("secret_decoy", declaration_names(snapshot["payload"]))
        expected = deepcopy(first)
        await self.owner.project_metadata("left", metadata={
            "analyzer_hints": {"symbols": [{"name": "ForgedAnnotationClass", "kind": "Class"}],
                               "trusted": True}}, expected_revision=0,
            expected_run_id=self.publications["left"]["run_id"])
        self.assertEqual(await self.runtime.capture_learning_snapshots(["left", "right"]), expected)
        (self.directory / "left" / "main.py").write_text("def changed_live():\n    return 0\n")
        (self.directory / "right" / "main.py").unlink()
        first[0]["payload"]["metadata"]["project_id"] = "forged"
        first[0]["sources"][0]["symbols"].clear()
        self.assertEqual(await self.runtime.capture_learning_snapshots(["left", "right"]), expected)
        await self.runtime.close()
        reopened = self.runtime_module.EmbeddedRuntime(str(self.directory / "state"), 3)
        try:
            with patch.object(reopened, "_get_encoder", AsyncMock(side_effect=AssertionError("model touched"))):
                self.assertEqual(await reopened.capture_learning_snapshots(["left", "right"]), expected)
        finally:
            await reopened.close()

    async def test_profiles_comparison_and_one_sided_reindex_preserve_scope(self):
        gateway, adapter = self.gateway()
        profile = await gateway.profile("left")
        self.assertEqual(profile["status"], "ok")
        self.assertEqual(profile["operation"], "profile")
        self.assert_source_receipts(profile["sources"], ["left"])
        self.assertEqual(profile["result"]["metadata"]["run_id"], self.publications["left"]["run_id"])
        comparison = await gateway.compare("left", "right")
        self.assertEqual(comparison["status"], "ok")
        self.assert_source_receipts(comparison["sources"], ["left", "right"])
        self.assertEqual([citation["project_id"] for citation in comparison["publications"]], ["left", "right"])
        for key, project in (("reference_profile", "left"), ("target_profile", "right")):
            self.assertEqual(comparison["result"][key]["metadata"]["run_id"], self.publications[project]["run_id"])
        previous_right = deepcopy(comparison["publications"][1])
        self.sources["left"]["main.py"] = "def replacement():\n    return 3\n"
        (self.directory / "left" / "main.py").write_text(self.sources["left"]["main.py"])
        self.publications["left"] = await self.owner.index(str(self.directory / "left"), "left",
            sorted(self.sources["left"]), embed=fixture_embed, encoder_id="fixture-v1")
        current = await gateway.compare("left", "right")
        self.assertEqual(current["publications"][1], previous_right)
        self.assertNotEqual(current["publications"][0]["run_id"], comparison["publications"][0]["run_id"])
        left_names = {symbol["name"] for source in current["sources"] if source["project_id"] == "left"
                      for symbol in source["symbols"]}
        self.assertIn("replacement", left_names)
        self.assertNotIn("authenticate", left_names)
        self.assertEqual(len(adapter.calls), 3)

    async def test_partial_file_selection_is_explicit_and_limits_are_strict(self):
        gateway, _ = self.gateway()
        result = await gateway.profile("left", max_files=1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([source["file_path"] for source in result["sources"]], ["helper.py"])
        coverage = result["coverage"][0]
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["published_files"], 2)
        self.assertEqual(coverage["included_files"], 1)
        self.assertEqual(coverage["omitted_files"], 1)
        for limit in (0, 65, True, "2"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                await self.runtime.capture_learning_snapshots(["left"], max_files=limit)

    async def test_missing_publication_and_disabled_adapter_do_not_execute_analysis(self):
        gateway, adapter = self.gateway()
        with self.assertRaises(self.snapshot_module.LearningSnapshotError) as caught:
            await self.runtime.capture_learning_snapshots(["missing"])
        self.assertEqual(caught.exception.code, "not-published")
        result = await gateway.profile("missing")
        self.assertEqual(result["status"], "not-published")
        self.assertNotIn("result", result)
        self.assertEqual(adapter.calls, [])
        gateway, adapter = self.gateway(EchoAdapter(self.runtime, status="disabled"))
        with patch.object(self.runtime, "capture_learning_snapshots", AsyncMock(
                side_effect=AssertionError("Disabled adapter touched publication state"))):
            result = await gateway.profile("left")
        self.assertEqual(result["status"], "disabled")
        self.assertNotIn("result", result)
        self.assertEqual(adapter.calls, [])

    async def test_corrupt_stored_source_or_parser_facts_refuse_analysis(self):
        for field, value in (("content", "def forged():\n    pass\n"), ("facts_json", "{}")):
            with self.subTest(field=field):
                async with self.owner.graph.session() as session:
                    async def corrupt(transaction):
                        await transaction.run(f"MATCH (s:SourceEvidence {{id:$id}}) SET s.{field}=$value",
                                              id="left:file:main.py", value=value)
                    await session.execute_write(corrupt)
                gateway, adapter = self.gateway()
                result = await gateway.profile("left")
                self.assertEqual(result["status"], "invalid-evidence")
                self.assertNotIn("result", result)
                self.assertEqual(adapter.calls, [])
                self.publications["left"] = await self.owner.index(str(self.directory / "left"), "left",
                    sorted(self.sources["left"]), embed=fixture_embed, encoder_id="fixture-v1")

    async def test_untrusted_worker_cannot_forge_publication_identity(self):
        async def forge(operation, payload, reference_payload, target_payload):
            # Mutate the input too: validation must compare against the captured
            # publication receipt, rather than trusting the worker's object.
            payload["metadata"]["project_id"] = "right"
            profile = EchoAdapter.profile(payload)
            return {"ok": True, "result": profile, "backend": {"name": "RepoAnalyzer"}}

        gateway, adapter = self.gateway(EchoAdapter(self.runtime, hook=forge))
        result = await gateway.profile("left")
        self.assertNotIn(result["status"], {"ok", "partial"})
        self.assertNotIn("result", result)
        self.assertEqual(len(adapter.calls), 1)
        fresh = await self.runtime.capture_learning_snapshots(["left"])
        self.assertEqual(fresh[0]["citation"]["project_id"], "left")
        self.assert_source_receipts(fresh[0]["sources"], ["left"])

    async def test_worker_file_evidence_is_bound_to_parent_owned_citations(self):
        async def evidence_result(operation, payload, reference_payload, target_payload):
            profile = EchoAdapter.profile(payload)
            profile["patterns"] = [{"evidence": [{"path": "main.py", "citation": {
                "project_id": "foreign", "run_id": "forged", "source_sha256": "fake"}}]}]
            return {"ok": True, "result": profile, "backend": {"name": "RepoAnalyzer"}}

        gateway, _ = self.gateway(EchoAdapter(self.runtime, hook=evidence_result))
        result = await gateway.profile("left")
        self.assertEqual(result["status"], "ok")
        citation = result["result"]["patterns"][0]["evidence"][0]["citation"]
        self.assertEqual(citation["project_id"], "left")
        self.assertEqual(citation["run_id"], self.publications["left"]["run_id"])
        self.assertEqual(citation["file_path"], "main.py")
        self.assertEqual(citation["source_sha256"], hashlib.sha256(self.sources["left"]["main.py"].encode()).hexdigest())
        self.assertEqual(result["findings_status"], "analyzer-inference")
        self.assertEqual(result["evidence_status"], "verified-published-snapshot")

    async def test_worker_cannot_attach_unbound_citations_to_pathless_evidence(self):
        async def evidence_result(operation, payload, reference_payload, target_payload):
            profile = EchoAdapter.profile(payload)
            profile["patterns"] = [{"evidence": [{"description": "unlocated inference", "citation": {
                "project_id": "foreign", "run_id": "forged", "source_sha256": "fake"}}]}]
            return {"ok": True, "result": profile, "backend": {"name": "RepoAnalyzer"}}

        gateway, _ = self.gateway(EchoAdapter(self.runtime, hook=evidence_result))
        result = await gateway.profile("left")
        if result["status"] in {"ok", "partial"}:
            self.assertNotIn("citation", result["result"]["patterns"][0]["evidence"][0])
        else:
            self.assertNotIn("result", result)

    async def test_worker_cannot_hide_foreign_citations_inside_file_evidence(self):
        async def evidence_result(operation, payload, reference_payload, target_payload):
            profile = EchoAdapter.profile(payload)
            profile["patterns"] = [{"evidence": [{"path": "main.py", "metadata": {
                "citation": {"project_id": "foreign", "run_id": "forged"}}}]}]
            return {"ok": True, "result": profile, "backend": {"name": "RepoAnalyzer"}}

        gateway, _ = self.gateway(EchoAdapter(self.runtime, hook=evidence_result))
        result = await gateway.profile("left")
        if result["status"] in {"ok", "partial"}:
            self.assertNotIn("citation", result["result"]["patterns"][0]["evidence"][0]["metadata"])
        else:
            self.assertNotIn("result", result)

    async def test_comparison_outer_evidence_binds_both_scopes_and_refuses_foreign_paths(self):
        evidence_path = "main.py"

        async def comparison_result(operation, payload, reference_payload, target_payload):
            reference = EchoAdapter.profile(reference_payload)
            target = EchoAdapter.profile(target_payload)
            for profile in (reference, target):
                profile["patterns"] = [{"evidence": [{"path": "main.py"}]}]
            result = {"reference_profile": reference, "target_profile": target,
                      "recommendations": [{"evidence": [{"path": evidence_path}]}]}
            return {"ok": True, "result": result, "backend": {"name": "RepoAnalyzer"}}

        gateway, _ = self.gateway(EchoAdapter(self.runtime, hook=comparison_result))
        result = await gateway.compare("left", "right")
        self.assertEqual(result["status"], "ok")
        citations = result["result"]["recommendations"][0]["evidence"][0]["citations"]
        self.assertEqual([citation["project_id"] for citation in citations], ["left", "right"])
        for citation in citations:
            project = citation["project_id"]
            self.assertEqual(citation["run_id"], self.publications[project]["run_id"])
            self.assertEqual(citation["source_sha256"], hashlib.sha256(self.sources[project]["main.py"].encode()).hexdigest())
        for key, project in (("reference_profile", "left"), ("target_profile", "right")):
            scoped = result["result"][key]["patterns"][0]["evidence"][0]["citation"]
            self.assertEqual(scoped["project_id"], project)
            self.assertNotIn("citations", result["result"][key]["patterns"][0]["evidence"][0])
        evidence_path = "unlisted.py"
        result = await gateway.compare("left", "right")
        self.assertEqual(result["status"], "invalid-analyzer-result")
        self.assertNotIn("result", result)

    async def test_empty_publication_is_explicit_without_analyzer_execution(self):
        root = self.directory / "empty"
        root.mkdir()
        publication = await self.owner.index(str(root), "empty", [], embed=fixture_embed, encoder_id="fixture-v1")
        captured = (await self.runtime.capture_learning_snapshots(["empty"]))[0]
        self.assertEqual(captured["citation"]["run_id"], publication["run_id"])
        self.assertEqual(captured["sources"], [])
        self.assertTrue(captured["coverage"]["complete"])
        gateway, adapter = self.gateway()
        for result in (await gateway.profile("empty"), await gateway.compare("left", "empty")):
            self.assertEqual(result["status"], "empty-publication")
            self.assertNotIn("result", result)
        self.assertEqual(adapter.calls, [])

    async def test_mutated_declarations_and_old_outline_provenance_are_refused(self):
        captured = (await self.runtime.capture_learning_snapshots(["left"]))[0]
        declaration = next(symbol for source in captured["sources"] for symbol in source["symbols"]
                           if symbol["name"] == "Policy")
        async with self.owner.graph.session() as session:
            async def mutate_declaration(transaction):
                await transaction.run("MATCH (s:Class {id:$id}) SET s.name=$name",
                                      id=declaration["id"], name="UnverifiedClass")
            await session.execute_write(mutate_declaration)
        gateway, adapter = self.gateway()
        result = await gateway.profile("left")
        self.assertEqual(result["status"], "invalid-evidence")
        self.assertNotIn("result", result)
        self.assertEqual(adapter.calls, [])
        self.publications["left"] = await self.owner.index(str(self.directory / "left"), "left",
            sorted(self.sources["left"]), embed=fixture_embed, encoder_id="fixture-v1")
        # Simulate a valid earlier manifest rather than corrupting its hash.
        manifest = deepcopy(self.publications["left"]["manifest"])
        for source in manifest["files"]:
            del source["outline_sha256"]
        del manifest["sha256"]
        manifest["sha256"] = hashlib.sha256(self.snapshot_module.canonical(manifest).encode()).hexdigest()
        async with self.owner.graph.session() as session:
            async def remove_outline_provenance(transaction):
                await transaction.run("MATCH (p:OutlinePublication {id:$id}) SET p.manifest_json=$manifest",
                                      id="left", manifest=self.snapshot_module.canonical(manifest))
            await session.execute_write(remove_outline_provenance)
        result = await gateway.profile("left")
        self.assertEqual(result["status"], "reindex-required-for-outline-provenance")
        self.assertNotIn("result", result)
        self.assertEqual(adapter.calls, [])

    async def test_symbol_budget_refuses_silent_declaration_truncation(self):
        root = self.directory / "large"
        root.mkdir()
        (root / "main.py").write_text("\n".join(f"def function_{index}():\n    return {index}\n"
                                                  for index in range(129)))
        await self.owner.index(str(root), "large", ["main.py"], embed=fixture_embed, encoder_id="fixture-v1")
        gateway, adapter = self.gateway()
        result = await gateway.profile("large")
        self.assertEqual(result["status"], "snapshot-too-large")
        self.assertNotIn("result", result)
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
