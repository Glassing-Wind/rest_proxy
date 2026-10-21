"""Offline evidence workflow acceptance using synthetic private temporary stores.

No environment variables, network, live services or personal case records required.
Run with .venv/bin/python test_evidence_workspace.py. Image-region tests use Pillow
when installed; text, review and recovery behavior needs only the standard library.
"""

import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from memory import evidence_workspace as evidence


REPO_ROOT = Path(__file__).resolve().parent
ORIGINAL = "Collected café record 😀\nThe sender requested a payment.\nNo ownership was verified.\n"
AMENDMENT = "The source records a payment request; ownership remains unresolved."


def package(case_id="fixture-case"):
    """A source claim and a separately inferred relationship with exact citations."""
    return {
        "schema_version": 1,
        "case_id": case_id,
        "title": "Synthetic evidence review",
        "coverage": {"status": "partial", "notes": "Only the supplied fixture is included."},
        "sources": [{
            "id": "message", "label": "Synthetic message", "kind": "message_export",
            "media_type": "text/plain", "text": ORIGINAL,
            "sha256": hashlib.sha256(ORIGINAL.encode()).hexdigest(),
            "observed_at": "2025-04-05T12:00:00Z",
            "original_reference": "synthetic:message-1", "source_revision": "fixture-v1",
        }],
        "entities": [
            {"id": "sender", "label": "Unverified sender", "kind": "account"},
            {"id": "payment", "label": "Requested payment", "kind": "event"},
        ],
        "relationships": [
            {
                "id": "request", "source": "sender", "target": "payment",
                "predicate": "requested", "statement": "The sender requested a payment.",
                "evidence_status": "source_claim",
                "citations": [{"source_id": "message", "start_line": 2, "end_line": 2,
                               "quote": "The sender requested a payment."}],
            },
            {
                "id": "ownership", "source": "sender", "target": "payment",
                "predicate": "may_control", "statement": "The sender owns the receiving account.",
                "evidence_status": "inference", "explanation": "A hypothesis requiring review.",
                "citations": [{"source_id": "message", "start_line": 3, "end_line": 3,
                               "quote": "No ownership was verified."}],
            },
        ],
    }


def rewrite_archive(data, transform):
    """Create a modified untrusted archive without extracting anything to disk."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            name, content = transform(info.filename, source.read(info.filename))
            target.writestr(name, content)
    return output.getvalue()


def rewrite_snapshot_with_valid_manifest(data, mutate, *, replacement_report=None):
    """Alter metadata and recompute checksums to exercise semantic restore checks."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        files = {item.filename: source.read(item.filename) for item in source.infolist()}
    snapshot = json.loads(files["case.json"])
    mutate(snapshot)

    def encode(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()

    snapshot["baseline_hash"] = hashlib.sha256(encode(snapshot["baseline"])).hexdigest()
    files["case.json"] = encode(snapshot)
    if replacement_report is not None:
        files["report.md"] = replacement_report
    manifest = json.loads(files["manifest.json"])
    manifest["revision"] = snapshot["revision"]
    for item in manifest["files"]:
        item["bytes"] = len(files[item["path"]])
        item["sha256"] = hashlib.sha256(files[item["path"]]).hexdigest()
    files["manifest.json"] = encode(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        for name, content in files.items():
            target.writestr(name, content)
    return output.getvalue()


class EvidenceWorkspaceAcceptance(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.state = self.root / "workspace"
        self.store = evidence.EvidenceWorkspaceStore(self.state)

    def import_fixture(self, case_id="fixture-case"):
        return self.store.import_package(package(case_id))

    def review(self, decision="accepted", expected_revision=1, **kwargs):
        return self.store.review(
            "fixture-case", "request", decision=decision, reviewer="fixture-reviewer",
            reason="The cited source supports this wording.", expected_revision=expected_revision,
            **kwargs,
        )

    def test_import_snapshot_survives_reopen_without_source_or_services(self):
        result = self.import_fixture()
        self.assertEqual(result["case_id"], "fixture-case")
        self.assertEqual(result["revision"], 1)
        reopened = evidence.EvidenceWorkspaceStore(self.state)
        self.assertEqual(reopened.get_case("fixture-case")["revision"], 1)
        self.assertEqual(reopened.get_source_content("fixture-case", "message"),
                         (ORIGINAL.encode(), "text/plain"))
        self.assertEqual(len(reopened.list_cases()["cases"]), 1)

    def test_source_recovery_in_an_independent_python_process(self):
        self.import_fixture()
        code = (
            "import hashlib,json,sys; "
            "from memory.evidence_workspace import EvidenceWorkspaceStore; "
            "s=EvidenceWorkspaceStore(sys.argv[1]); "
            "b,t=s.get_source_content('fixture-case','message'); "
            "print(json.dumps({'sha256':hashlib.sha256(b).hexdigest(),'media_type':t,"
            "'revision':s.get_case('fixture-case')['revision']}))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code, str(self.state)], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=15, check=True,
        )
        recovered = json.loads(completed.stdout)
        self.assertEqual(recovered["sha256"], hashlib.sha256(ORIGINAL.encode()).hexdigest())
        self.assertEqual(recovered["media_type"], "text/plain")
        self.assertEqual(recovered["revision"], 1)

    def test_bounded_source_pages_preserve_utf8_line_identity(self):
        self.import_fixture()
        first = self.store.get_source("fixture-case", "message", start_line=1, max_lines=1)
        self.assertEqual(first["numbered_lines"], [{"number": 1, "text": "Collected café record 😀"}])
        self.assertEqual(first["next_line"], 2)
        second = self.store.get_source("fixture-case", "message", start_line=2, max_lines=2)
        self.assertEqual(second["numbered_lines"], [
            {"number": 2, "text": "The sender requested a payment."},
            {"number": 3, "text": "No ownership was verified."},
        ])
        self.assertIsNone(second["next_line"])
        for kwargs in ({"start_line": 4}, {"start_line": 0}, {"max_lines": 201},
                       {"max_lines": True}, {"start_line": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(evidence.WorkspaceError):
                self.store.get_source("fixture-case", "message", **kwargs)

    def test_case_and_store_isolation_with_colliding_source_ids(self):
        self.import_fixture()
        other = package("second-case")
        other["sources"][0]["text"] = ORIGINAL.replace("café", "other")
        other["sources"][0].pop("sha256")
        self.store.import_package(other)
        self.assertEqual(self.store.get_source_content("fixture-case", "message")[0], ORIGINAL.encode())
        self.assertNotEqual(self.store.get_source_content("second-case", "message")[0], ORIGINAL.encode())
        isolated = evidence.EvidenceWorkspaceStore(self.root / "separate-workspace")
        self.assertEqual(isolated.list_cases()["cases"], [])
        with self.assertRaises(evidence.WorkspaceError):
            isolated.get_source_content("fixture-case", "message")

    def test_same_import_is_idempotent_and_changed_import_cannot_overwrite(self):
        self.import_fixture()
        duplicate = self.import_fixture()
        self.assertEqual(duplicate["revision"], 1)
        changed = package()
        changed["title"] = "Replacement pretending to be the same case"
        with self.assertRaises(evidence.WorkspaceConflict):
            self.store.import_package(changed)
        self.assertEqual(self.store.get_case("fixture-case")["title"], "Synthetic evidence review")
        self.assertEqual(self.store.get_source_content("fixture-case", "message")[0], ORIGINAL.encode())

    def test_mutating_callers_package_cannot_change_saved_case(self):
        supplied = package()
        self.store.import_package(supplied)
        supplied["sources"][0]["text"] = "Mutated by the caller after import."
        supplied["relationships"][0]["statement"] = "A different finding."
        supplied["entities"][0]["label"] = "A different entity."
        saved = self.store.get_case("fixture-case")
        relation = next(item for item in saved["relationships"] if item["id"] == "request")
        self.assertEqual(relation["statement"], "The sender requested a payment.")
        self.assertEqual(self.store.get_source_content("fixture-case", "message")[0], ORIGINAL.encode())

    def test_case_listing_does_not_dump_raw_original_content(self):
        self.import_fixture()
        listing = self.store.list_cases()
        self.assertEqual(len(listing["cases"]), 1)
        self.assertNotIn("Collected café record", json.dumps(listing, ensure_ascii=False))

    def test_invalid_packages_leave_no_partial_import(self):
        invalid = []
        for category in ("sources", "entities", "relationships"):
            changed = package()
            changed[category].append(copy.deepcopy(changed[category][0]))
            invalid.append((f"duplicate {category}", changed))
        for field, value in (("source", "unknown"), ("target", "unknown"),
                             ("evidence_status", "proven_identity")):
            changed = package()
            changed["relationships"][0][field] = value
            invalid.append((f"invalid relationship {field}", changed))
        for key, value in (("source_id", "unknown"), ("start_line", 0),
                           ("end_line", 200), ("quote", "Text absent from the source")):
            changed = package()
            changed["relationships"][0]["citations"][0][key] = value
            invalid.append((f"invalid citation {key}", changed))
        changed = package()
        changed["sources"][0]["sha256"] = "0" * 64
        invalid.append(("mismatched source hash", changed))
        changed = package()
        changed["sources"][0]["observed_at"] = "2025-04-05T12:00:00"
        invalid.append(("ambiguous timestamp", changed))
        changed = package()
        changed["sources"][0]["content_base64"] = base64.b64encode(b"second representation").decode()
        invalid.append(("ambiguous source representation", changed))
        for description, changed in invalid:
            with self.subTest(description=description):
                with self.assertRaises(evidence.WorkspaceError):
                    self.store.import_package(changed)
                self.assertEqual(self.store.list_cases()["cases"], [])

    def test_unsafe_identifiers_and_invalid_binary_text_are_rejected(self):
        for identifier in ("../escape", "/absolute", "a/b", "a\\b", "case\x00id", ""):
            with self.subTest(identifier=identifier), self.assertRaises(evidence.WorkspaceError):
                self.store.import_package(package(identifier))
        changed = package()
        source = changed["sources"][0]
        source.pop("text")
        source.pop("sha256")
        source["content_base64"] = base64.b64encode(b"\xffinvalid UTF-8").decode()
        with self.assertRaises(evidence.WorkspaceError):
            self.store.import_package(changed)
        source["content_base64"] = "invalid*base64"
        with self.assertRaises(evidence.WorkspaceError):
            self.store.import_package(changed)

    def test_current_source_check_never_replaces_original_snapshot(self):
        self.import_fixture()
        matching = self.store.check_source(
            "fixture-case", "message", expected_revision=1, content=ORIGINAL.encode(),
        )
        changed = self.store.check_source(
            "fixture-case", "message", expected_revision=matching["revision"],
            content=b"An edited current file.",
        )
        source = next(item for item in self.store.get_case("fixture-case")["sources"]
                      if item["id"] == "message")
        self.assertEqual(source["freshness"], "changed")
        missing = self.store.check_source(
            "fixture-case", "message", expected_revision=changed["revision"], missing=True,
        )
        reopened = evidence.EvidenceWorkspaceStore(self.state)
        self.assertEqual(reopened.get_source_content("fixture-case", "message")[0], ORIGINAL.encode())
        self.assertEqual(reopened.get_case("fixture-case")["revision"], missing["revision"])
        source = next(item for item in reopened.get_case("fixture-case")["sources"]
                      if item["id"] == "message")
        self.assertEqual(source["freshness"], "missing")

    def test_unavailable_source_is_a_gap_and_cannot_support_a_citation(self):
        incomplete = package()
        incomplete["sources"].append({
            "id": "missing-export", "label": "Export never acquired", "kind": "message_export",
            "media_type": "text/plain", "availability": "unavailable",
        })
        self.store.import_package(incomplete)
        source = next(item for item in self.store.get_case("fixture-case")["sources"]
                      if item["id"] == "missing-export")
        self.assertEqual(source["integrity"], "unavailable")
        with self.assertRaises(evidence.WorkspaceError):
            self.store.get_source_content("fixture-case", "missing-export")
        invalid = copy.deepcopy(incomplete)
        invalid["case_id"] = "unsupported-citation"
        invalid["relationships"][0]["citations"][0]["source_id"] = "missing-export"
        with self.assertRaises(evidence.WorkspaceError):
            self.store.import_package(invalid)

    def test_ambiguous_current_check_is_not_recorded_as_fresh(self):
        self.import_fixture()
        for kwargs in ({}, {"content": ORIGINAL.encode(), "missing": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(evidence.WorkspaceError):
                self.store.check_source("fixture-case", "message", expected_revision=1, **kwargs)
        self.assertEqual(self.store.get_case("fixture-case")["revision"], 1)

    def test_source_corruption_blocks_reads_report_and_backup(self):
        self.import_fixture()
        db = sqlite3.connect(self.state / "workspaces.sqlite3")
        try:
            with db:
                db.execute("UPDATE sources SET content=? WHERE case_id=? AND source_id=?",
                           (b"Corrupted database bytes", "fixture-case", "message"))
        finally:
            db.close()
        reopened = evidence.EvidenceWorkspaceStore(self.state)
        for operation in (
            lambda: reopened.get_case("fixture-case"),
            lambda: reopened.get_source("fixture-case", "message"),
            lambda: reopened.get_source_content("fixture-case", "message"),
            lambda: reopened.export_report("fixture-case"),
            lambda: reopened.export_backup("fixture-case"),
        ):
            with self.subTest(operation=operation), self.assertRaises(evidence.WorkspaceIntegrityError):
                operation()

    def test_text_and_source_count_budgets_reject_before_partial_persistence(self):
        too_large = package()
        too_large["sources"][0]["text"] = "x" * (256 * 1024 + 1)
        too_large["sources"][0].pop("sha256")
        too_many = package()
        too_many["sources"].extend({
            "id": f"extra-{index}", "label": "Missing synthetic export", "kind": "fixture",
            "media_type": "text/plain", "availability": "unavailable",
        } for index in range(64))
        for invalid in (too_large, too_many):
            with self.subTest(sources=len(invalid["sources"])), self.assertRaises(evidence.WorkspaceError):
                self.store.import_package(invalid)
        self.assertEqual(self.store.list_cases()["cases"], [])

    def test_two_writers_cannot_review_the_same_revision(self):
        self.import_fixture()
        second = evidence.EvidenceWorkspaceStore(self.state)
        accepted = self.review()
        self.assertEqual(accepted["revision"], 2)
        with self.assertRaises(evidence.WorkspaceConflict):
            second.review(
                "fixture-case", "request", decision="rejected", reviewer="other-reviewer",
                reason="A stale decision must not win.", expected_revision=1,
            )
        self.assertEqual(second.get_case("fixture-case")["revision"], 2)

    def test_review_and_source_check_share_one_concurrency_revision(self):
        self.import_fixture()
        self.review()
        with self.assertRaises(evidence.WorkspaceConflict):
            self.store.check_source(
                "fixture-case", "message", expected_revision=1, content=ORIGINAL.encode(),
            )
        detail = self.store.get_case("fixture-case")
        self.assertEqual(detail["revision"], 2)
        self.assertEqual(detail["sources"][0]["freshness"], "not-checked")

    def test_review_requires_reason_and_amendment_requires_new_statement(self):
        self.import_fixture()
        for decision, reason, statement in (("accepted", "", None), ("rejected", "   ", None),
                                            ("amended", "Correction", None),
                                            ("amended", "Correction", "   "),
                                            ("auto_proven", "Not supported", None)):
            with self.subTest(decision=decision, statement=statement):
                with self.assertRaises(evidence.WorkspaceError):
                    self.store.review(
                        "fixture-case", "request", decision=decision, reviewer="fixture-reviewer",
                        reason=reason, expected_revision=1, statement=statement,
                    )
                self.assertEqual(self.store.get_case("fixture-case")["revision"], 1)

    def test_review_history_preserves_baseline_and_source_claim_status(self):
        self.import_fixture()
        self.review()
        self.review(decision="amended", expected_revision=2, statement=AMENDMENT)
        self.review(decision="rejected", expected_revision=3)
        reopened = evidence.EvidenceWorkspaceStore(self.state)
        detail = reopened.get_case("fixture-case")
        self.assertEqual(detail["revision"], 4)
        relation = next(item for item in detail["relationships"] if item["id"] == "request")
        self.assertEqual(relation["statement"], "The sender requested a payment.")
        self.assertEqual(relation["evidence_status"], "source_claim")
        self.assertEqual(relation["review"]["decision"], "rejected")
        self.assertEqual([event["decision"] for event in relation["history"]],
                         ["accepted", "amended", "rejected"])
        self.assertEqual(reopened.get_source_content("fixture-case", "message")[0], ORIGINAL.encode())

    def test_accepting_amendment_keeps_effective_correction_after_restore(self):
        self.import_fixture()
        self.review(decision="amended", statement=AMENDMENT)
        self.review(decision="accepted", expected_revision=2)
        reopened = evidence.EvidenceWorkspaceStore(self.state)
        relation = next(item for item in reopened.get_case("fixture-case")["relationships"]
                        if item["id"] == "request")
        self.assertEqual(relation["statement"], "The sender requested a payment.")
        self.assertEqual(relation["effective_statement"], AMENDMENT)
        self.assertEqual(relation["review"]["decision"], "accepted")
        self.assertEqual(relation["evidence_status"], "source_claim")
        restored = evidence.EvidenceWorkspaceStore(self.root / "accepted-amendment-restore")
        restored.restore_backup(reopened.export_backup("fixture-case"))
        report = restored.export_report("fixture-case")
        self.assertIn(AMENDMENT, report)
        self.assertNotIn("The sender requested a payment.", report)

    def test_report_uses_effective_reviewed_statement_and_omits_raw_source(self):
        self.import_fixture()
        self.review(decision="amended", statement=AMENDMENT)
        report = self.store.export_report("fixture-case")
        self.assertIn(AMENDMENT, report)
        self.assertNotIn("The sender requested a payment.", report)
        self.assertNotIn("Collected café record 😀", report)
        self.assertNotIn("The sender owns the receiving account.", report)
        self.assertIn("partial", report.lower())

    def test_backup_is_deterministic_restores_review_and_refuses_overwrite(self):
        self.import_fixture()
        self.review(decision="amended", statement=AMENDMENT)
        first = self.store.export_backup("fixture-case")
        self.assertEqual(first, self.store.export_backup("fixture-case"))
        restored = evidence.EvidenceWorkspaceStore(self.root / "restored")
        result = restored.restore_backup(first)
        self.assertEqual(result["case_id"], "fixture-case")
        self.assertEqual(restored.get_case("fixture-case")["revision"], 2)
        self.assertEqual(restored.get_source_content("fixture-case", "message"),
                         (ORIGINAL.encode(), "text/plain"))
        self.assertEqual(restored.export_report("fixture-case"), self.store.export_report("fixture-case"))
        with self.assertRaises(evidence.WorkspaceConflict):
            restored.restore_backup(first)
        renamed = restored.restore_backup(first, case_id="renamed-case")
        self.assertEqual(renamed["case_id"], "renamed-case")
        self.assertEqual(restored.get_source_content("renamed-case", "message")[0], ORIGINAL.encode())
        self.assertEqual(restored.get_case("renamed-case")["origin_case_id"], "fixture-case")
        self.assertEqual(self.store.get_case("fixture-case")["case_id"], "fixture-case")

    def test_recovery_keeps_interleaved_review_and_current_check_history(self):
        self.import_fixture()
        self.store.check_source("fixture-case", "message", expected_revision=1, content=ORIGINAL.encode())
        self.review(decision="amended", expected_revision=2, statement=AMENDMENT)
        self.store.check_source("fixture-case", "message", expected_revision=3, content=b"Edited current file")
        restored = evidence.EvidenceWorkspaceStore(self.root / "mixed-history-restore")
        restored.restore_backup(self.store.export_backup("fixture-case"))
        detail = restored.get_case("fixture-case")
        self.assertEqual(detail["revision"], 4)
        self.assertEqual(detail["sources"][0]["freshness"], "changed")
        self.assertEqual(detail["sources"][0]["check"]["revision"], 4)
        relation = next(item for item in detail["relationships"] if item["id"] == "request")
        self.assertEqual(relation["effective_statement"], AMENDMENT)
        self.assertEqual([item["revision"] for item in relation["history"]], [3])
        self.assertEqual(restored.get_source_content("fixture-case", "message")[0], ORIGINAL.encode())
        with zipfile.ZipFile(io.BytesIO(restored.export_backup("fixture-case"))) as archive:
            events = json.loads(archive.read("case.json"))["events"]
        self.assertEqual([item["kind"] for item in events], ["source_check", "review", "source_check"])

    def test_archive_traversal_duplicate_entries_and_garbage_leave_store_empty(self):
        self.import_fixture()
        backup = self.store.export_backup("fixture-case")
        targets = [b"not a ZIP archive"]
        for name in ("../outside.txt", "/absolute.txt", "nested/../../outside.txt"):
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(backup)) as source, zipfile.ZipFile(output, "w") as target:
                for info in source.infolist():
                    target.writestr(info.filename, source.read(info.filename))
                target.writestr(name, b"must not be extracted")
            targets.append(output.getvalue())
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(backup)) as source, zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info.filename))
            duplicate = source.infolist()[0]
            with self.assertWarns(UserWarning):
                target.writestr(duplicate.filename, source.read(duplicate.filename))
        targets.append(output.getvalue())
        restored = evidence.EvidenceWorkspaceStore(self.root / "empty-restore")
        for data in targets:
            with self.subTest(archive_size=len(data)):
                with self.assertRaises(evidence.WorkspaceError):
                    restored.restore_backup(data)
                self.assertEqual(restored.list_cases()["cases"], [])
        self.assertFalse((self.root / "outside.txt").exists())

    def test_backup_rejects_changed_original_even_with_valid_zip_checksums(self):
        self.import_fixture()
        backup = self.store.export_backup("fixture-case")
        changed_count = 0

        def tamper(name, content):
            nonlocal changed_count
            if content == ORIGINAL.encode():
                changed_count += 1
                return name, b"Changed after export but still valid ZIP."
            return name, content

        corrupted = rewrite_archive(backup, tamper)
        self.assertEqual(changed_count, 1, "Backup must retain exact original bytes as an exhibit")
        restored = evidence.EvidenceWorkspaceStore(self.root / "tampered-restore")
        with self.assertRaises(evidence.WorkspaceError):
            restored.restore_backup(corrupted)
        self.assertEqual(restored.list_cases()["cases"], [])

    def test_restore_rechecks_references_and_history_after_valid_packet_checksums(self):
        self.import_fixture()
        self.review(decision="amended", statement=AMENDMENT)
        backup = self.store.export_backup("fixture-case")

        def unknown_entity(snapshot):
            snapshot["baseline"]["relationships"][0]["source"] = "unknown-entity"

        def unknown_review_relation(snapshot):
            snapshot["events"][0]["relationship_id"] = "unknown-relationship"

        def unordered_history(snapshot):
            snapshot["events"][0]["revision"] = 9

        def wrong_citation_quote(snapshot):
            snapshot["baseline"]["relationships"][0]["citations"][0]["quote"] = "Absent text"

        restored = evidence.EvidenceWorkspaceStore(self.root / "semantic-tamper-restore")
        for mutate in (unknown_entity, unknown_review_relation, unordered_history, wrong_citation_quote):
            corrupted = rewrite_snapshot_with_valid_manifest(backup, mutate)
            with self.subTest(mutation=mutate.__name__), self.assertRaises(evidence.WorkspaceError):
                restored.restore_backup(corrupted)
            self.assertEqual(restored.list_cases()["cases"], [])

    def test_rehashed_bundled_report_must_match_reviewed_recovery_state(self):
        self.import_fixture()
        self.review(decision="amended", statement=AMENDMENT)
        backup = self.store.export_backup("fixture-case")
        tampered = rewrite_snapshot_with_valid_manifest(
            backup, lambda snapshot: None,
            replacement_report=b"# Misleading report\nThis unreviewed identity is established.\n",
        )
        restored = evidence.EvidenceWorkspaceStore(self.root / "misleading-report-restore")
        with self.assertRaises(evidence.WorkspaceIntegrityError):
            restored.restore_backup(tampered)
        self.assertEqual(restored.list_cases()["cases"], [])

    def test_aggregate_recovery_budget_rejects_review_before_writing_history(self):
        self.import_fixture()
        before = self.store.export_backup("fixture-case")
        with zipfile.ZipFile(io.BytesIO(before)) as archive:
            current_metadata_bytes = len(archive.read("case.json"))
        with mock.patch.object(evidence, "MAX_ARCHIVE_JSON_BYTES", current_metadata_bytes):
            with self.assertRaises(evidence.WorkspaceError):
                self.review(decision="amended", statement=AMENDMENT)
        self.assertEqual(self.store.get_case("fixture-case")["revision"], 1)
        self.assertEqual(self.store.export_backup("fixture-case"), before)

    def test_rendered_report_budget_rejects_escape_amplification_before_commit(self):
        self.import_fixture()
        reason = "`" * 3999 + "r"
        statement = "*" * 3999 + "s"
        calibration = evidence.EvidenceWorkspaceStore(self.root / "report-budget-calibration")
        calibration.import_package(package())
        calibration.review(
            "fixture-case", "request", decision="amended", reviewer="fixture-reviewer",
            reason=reason, statement=statement, expected_revision=1,
        )
        with zipfile.ZipFile(io.BytesIO(calibration.export_backup("fixture-case"))) as archive:
            metadata_size = len(archive.read("case.json"))
            rendered_size = len(archive.read("report.md"))
        budget = metadata_size + 128
        self.assertGreater(rendered_size, budget)
        before = self.store.export_backup("fixture-case")
        with mock.patch.object(evidence, "MAX_ARCHIVE_JSON_BYTES", budget):
            with self.assertRaises(evidence.WorkspaceError):
                self.store.review(
                    "fixture-case", "request", decision="amended", reviewer="fixture-reviewer",
                    reason=reason, statement=statement, expected_revision=1,
                )
        self.assertEqual(self.store.get_case("fixture-case")["revision"], 1)
        self.assertEqual(self.store.export_backup("fixture-case"), before)

    def test_oversized_expanded_archive_is_rejected_without_extraction(self):
        self.import_fixture()
        backup = self.store.export_backup("fixture-case")
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(backup)) as source, zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename == "originals/message":
                    content = b"x" * (8 * 1024 * 1024 + 1)
                target.writestr(info.filename, content)
        self.assertLess(len(output.getvalue()), 20000)
        restored = evidence.EvidenceWorkspaceStore(self.root / "oversized-restore")
        with self.assertRaises(evidence.WorkspaceError):
            restored.restore_backup(output.getvalue())
        self.assertEqual(restored.list_cases()["cases"], [])

    def test_bzip2_and_lzma_recovery_compression_are_refused(self):
        self.import_fixture()
        backup = self.store.export_backup("fixture-case")
        restored = evidence.EvidenceWorkspaceStore(self.root / "unsupported-codec-restore")
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(backup)) as source, zipfile.ZipFile(
                    output, "w", compression=method) as target:
                for info in source.infolist():
                    target.writestr(info.filename, source.read(info.filename))
            with self.subTest(method=method), self.assertRaises(evidence.WorkspaceIntegrityError):
                restored.restore_backup(output.getvalue())
            self.assertEqual(restored.list_cases()["cases"], [])

    @unittest.skipUnless(os.name == "posix", "Private POSIX state permissions")
    def test_loose_permissions_and_symlink_state_are_rejected(self):
        self.import_fixture()
        self.assertEqual(self.state.stat().st_mode & 0o077, 0)
        databases = list(self.state.glob("*.sqlite*")) + list(self.state.glob("*.db"))
        self.assertTrue(databases, "The private workspace must persist its SQLite state")
        for path in databases:
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o077, 0)
        alias = self.root / "alias"
        alias.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(evidence.WorkspaceError):
            evidence.EvidenceWorkspaceStore(alias)
        os.chmod(self.state, 0o755)
        with self.assertRaises(evidence.WorkspaceError):
            evidence.EvidenceWorkspaceStore(self.state)

    @unittest.skipUnless(os.name == "posix", "Private POSIX database permissions")
    def test_loose_or_symlink_database_file_is_rejected(self):
        self.import_fixture()
        database = self.state / "workspaces.sqlite3"
        os.chmod(database, 0o644)
        with self.assertRaises(evidence.WorkspaceError):
            evidence.EvidenceWorkspaceStore(self.state)
        os.chmod(database, 0o600)
        moved = self.root / "moved.sqlite3"
        database.rename(moved)
        database.symlink_to(moved)
        original_bytes = moved.read_bytes()
        with self.assertRaises(evidence.WorkspaceError):
            evidence.EvidenceWorkspaceStore(self.state)
        self.assertEqual(moved.read_bytes(), original_bytes)

    def test_valid_image_region_and_invalid_region_are_distinguished(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is optional for image evidence")
        content = io.BytesIO()
        Image.new("RGB", (20, 10), "white").save(content, format="PNG")
        image = {
            "id": "photo", "label": "Synthetic image", "kind": "photo",
            "media_type": "image/png", "content_base64": base64.b64encode(content.getvalue()).decode(),
        }
        valid = package()
        valid["sources"].append(image)
        valid["relationships"][0]["citations"] = [{
            "source_id": "photo", "bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
            "quote": "Unverified transcription of the synthetic image",
        }]
        self.store.import_package(valid)
        self.assertEqual(self.store.get_source_content("fixture-case", "photo"),
                         (content.getvalue(), "image/png"))
        self.review()
        relation = self.store.get_case("fixture-case")["relationships"][0]
        self.assertEqual(relation["evidence_status"], "source_claim")
        self.assertEqual(relation["citations"][0]["transcription_status"], "unverified-image-transcription")
        restored = evidence.EvidenceWorkspaceStore(self.root / "image-restore")
        restored.restore_backup(self.store.export_backup("fixture-case"))
        self.assertEqual(restored.get_source_content("fixture-case", "photo")[0], content.getvalue())
        self.assertEqual(restored.get_case("fixture-case")["relationships"][0]["citations"],
                         relation["citations"])
        invalid = copy.deepcopy(valid)
        invalid["case_id"] = "bad-image-region"
        invalid["relationships"][0]["citations"][0]["bbox"]["width"] = 2
        with self.assertRaises(evidence.WorkspaceError):
            self.store.import_package(invalid)


if __name__ == "__main__":
    unittest.main()
