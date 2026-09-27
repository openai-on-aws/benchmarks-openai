"""Offline diagnosis invariants, accounting, evidence safety, and installed helpers."""

import copy
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bedrock-bench"
SKILL = PLUGIN / "skills/diagnose-failures"
sys.path.insert(0, str(PLUGIN / "scripts"))

from bedrock_bench import diagnosis


TARGET = {
    "id": "target-a", "runner": "codex", "provider": "amazon-bedrock",
    "model": "fixture-model", "region": "us-east-1", "reasoning_effort": "high",
}


def attempt(number=1, **changes):
    row = {
        "attempt_id": f"attempt-{number}", "task_id": f"task-{number}", "repetition": 1,
        "target": copy.deepcopy(TARGET), "runner_version": "fixture-version",
        "status": "completed", "success": True, "errors": [],
        "grading": {"success": True}, "cost_usd": 1.0, "known_cost_subtotal_usd": 1.0,
        "cost_basis": ["runner_estimate"], "accounting_complete": True,
        "trace": f"attempts/attempt-{number}/events.jsonl",
    }
    row.update(changes)
    return row


def failed(number=1, **changes):
    return attempt(number, **{
        "status": "task_failed", "success": False, "grading": {"success": False}, **changes,
    })


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.serial = 0

    def run_file(self, rows=None, *, traces=True, **changes):
        self.serial += 1
        rows = copy.deepcopy(rows if rows is not None else [attempt()])
        run_id = changes.pop("run_id", f"run-{self.serial}")
        root = self.directory / f"source-{self.serial}"
        root.mkdir()
        if traces:
            for row in rows:
                path = root / row["trace"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"type":"completed"}\n')
                row["trace_sha256"] = sha(path)
        run = {
            "schema_version": 1, "run_id": run_id, "status": "completed",
            "synthetic": False, "validation_only": False, "protocol_hash": "protocol-a",
            "attempts": rows,
            "schedule": [
                {"target": row["target"]["id"], "task": row["task_id"], "repetition": row["repetition"]}
                for row in rows
            ],
            **changes,
        }
        source = root / "run.json"
        source.write_text(json.dumps(run))
        return source, run

    def save(self, source, run):
        source.write_text(json.dumps(run))

    def reviewed_file(self, source, run):
        reviewed = copy.deepcopy(run)
        reviewed["name"] = "Reviewed accounting fixture"
        reviewed["attempts"][0].update(
            cost_usd=.25, known_cost_subtotal_usd=.25, cost_basis=["rate_card_estimate"],
            accounting_complete=True, usage={"input_tokens": 10, "output_tokens": 5},
            usage_granularity="reviewed_total", reported_usage_events=1,
            accounting_review={"method": "saved accounting correction"},
        )
        audit = source.parent / "accounting-audit.txt"
        audit.write_text("PRIVATE_AUDIT_CONTENT: source presence is not a pricing audit.\n")
        reviewed["accounting_review"] = {
            "original_run": source.name, "original_sha256": sha(source), "source": audit.name,
        }
        path = source.parent / "run-accounting-reviewed.json"
        self.save(path, reviewed)
        return path, reviewed, audit

    def trace(self, source, run, content, *, index=0, suffix=".jsonl", checksum=True):
        row = run["attempts"][index]
        row["trace"] = f"attempts/{row['attempt_id']}/evidence{suffix}"
        path = source.parent / row["trace"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)) and suffix == ".json":
            content = json.dumps(content)
        elif isinstance(content, list):
            content = "\n".join(json.dumps(event) for event in content) + "\n"
        path.write_text(content)
        if checksum:
            row["trace_sha256"] = sha(path)
        else:
            row.pop("trace_sha256", None)
        self.save(source, run)
        return path

    def data(self, source, **kwargs):
        return diagnosis.diagnosis_data([source], **kwargs)

    def test_full_population_outcomes_costs_and_bounded_details(self):
        rows = [
            attempt(1),
            failed(2, status="timeout", cost_usd=None, known_cost_subtotal_usd=.25, accounting_complete=False),
            failed(3, cost_usd=.5, known_cost_subtotal_usd=.5),
            failed(4, status="interrupted", cost_usd=None, known_cost_subtotal_usd=.1, accounting_complete=False),
            attempt(5, status="running", success=None, cost_usd=None, known_cost_subtotal_usd=0, accounting_complete=False),
        ]
        source, run = self.run_file(rows, status="interrupted")
        run["schedule"].append({"target": TARGET["id"], "task": "unrecorded-task", "repetition": 1})
        self.save(source, run)
        data = self.data(source, limit=1)
        counts = data["counts"]
        self.assertEqual(
            {key: counts[key] for key in ("attempts", "recorded_attempts", "successes", "failures",
                                         "interrupted_attempts", "missing_attempts", "unknown_outcomes")},
            {"attempts": 6, "recorded_attempts": 5, "successes": 1, "failures": 2,
             "interrupted_attempts": 1, "missing_attempts": 1, "unknown_outcomes": 1},
        )
        self.assertAlmostEqual(counts["known_cost_subtotal_usd"], 1.85)
        self.assertAlmostEqual(counts["failed_attempt_known_cost_subtotal_usd"], .75)
        self.assertIsNone(counts["total_cost_usd"])
        self.assertIsNone(counts["failed_attempt_cost_usd"])
        self.assertEqual(counts["unknown_cost_attempts"], 4)
        self.assertEqual(counts["failed_attempt_unknown_costs"], 1)
        self.assertAlmostEqual(counts["cost_coverage"], 2 / 6)
        self.assertAlmostEqual(counts["recorded_cost_coverage"], 2 / 5)
        self.assertEqual(len(data["records"]), 6)
        self.assertEqual(len(data["details"]), 1)
        self.assertEqual(data["details"][0]["attempt_id"], "attempt-2")
        self.assertEqual(data["coverage"]["details_omitted"], 5)
        self.assertEqual(sum(row["attempts"] for row in data["cells"]), 6)
        self.assertEqual(sum(row["failures"] for row in data["by_category"]), 2)
        missing = next(row for row in data["records"] if row["outcome"] == "missing")
        self.assertIsNone(missing["attempt_id"])
        self.assertIsNone(missing["cost_usd"])
        self.assertEqual(missing["task_id"], "unrecorded-task")
        self.assertTrue(data["runs"][0]["missing_attempts_known"])
        for row in data["records"]:
            self.assertEqual(row["run_id"], run["run_id"])
            self.assertEqual(row["source"], str(source))
            self.assertEqual(row["source_sha256"], sha(source))

    def test_explicit_timeout_and_upstream_timeout_have_grounded_citations(self):
        source, run = self.run_file([failed(status="runner_error")])
        path = self.trace(source, run, {
            "exception_info": {"exception_type": "AgentTimeoutError",
                               "exception_message": "agent exceeded the configured budget"},
            "verifier_result": None, "config": {"system_prompt": "NEVER_PUBLIC"},
        }, suffix=".json")
        row = run["attempts"][0]
        row["upstream"] = {"harness": "harbor", "result": "evidence.json"}
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["records"][0]["category"], "timeout")
        detail = data["details"][0]
        self.assertEqual(detail["classification"]["confidence"], "medium")
        self.assertEqual(detail["classification"]["source"], str(path))
        self.assertEqual(detail["classification"]["field"], "/exception_info.exception_type")
        self.assertEqual(detail["evidence"][0]["sha256"], sha(path))
        self.assertEqual(detail["evidence"][0]["kinds"], ["trace", "upstream_result"])
        self.assertEqual(len(detail["evidence"]), 1)
        row["status"] = "timeout"
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["records"][0]["confidence"], "high")
        self.assertEqual(data["details"][0]["classification"]["source"], str(source))
        self.assertEqual(data["details"][0]["classification"]["field"], "/attempts/0/status")

    def test_signature_taxonomy_requires_terminal_failure_evidence(self):
        examples = [
            ("NoCredentialsError: unable to locate credentials", "authentication"),
            ("AccessDeniedException", "authentication"),
            ("HTTP 429 TooManyRequests", "throttling"),
            ("RateLimitError", "throttling"),
            ("ModuleNotFoundError: fixture", "setup_error"),
            ("Cannot connect to the Docker daemon", "setup_error"),
            ("VerifierError", "grader_failure"),
            ("unclassified runner stopped", "runner_error"),
        ]
        for message, category in examples:
            with self.subTest(category=category, message=message):
                source, _ = self.run_file([failed(status="runner_error", errors=[message])])
                data = self.data(source)
                self.assertEqual(data["records"][0]["category"], category)
                self.assertEqual(data["counts"]["failures"], 1)
                if category != "runner_error":
                    self.assertEqual(data["details"][0]["classification"]["field"], "/attempts/0/errors/0")

    def test_recoverable_tool_error_never_sets_failure_category(self):
        for status, success, expected in [
            ("completed", True, "success"),
            ("task_failed", False, "grader_failure"),
            ("runner_error", False, "runner_error"),
        ]:
            with self.subTest(status=status):
                source, run = self.run_file([attempt(status=status, success=success, grading={"success": success})])
                self.trace(source, run, [
                    {"type": "tool", "name": "read_file", "arguments": "DO_NOT_EXECUTE",
                     "result": {"error": "FileNotFoundError"}},
                    {"type": "item.completed", "item": {
                        "type": "command_execution", "status": "failed", "exit_code": 1,
                        "command": "DO_NOT_EXECUTE", "aggregated_output": "AccessDeniedException"}},
                    {"type": "response_item", "payload": {
                        "type": "function_call_output",
                        "output": "Process exited with code 1\nOutput:\nThrottlingException"}},
                    {"type": "completed"},
                ])
                data = self.data(source)
                self.assertEqual(data["records"][0]["category"], expected)
                self.assertTrue(data["details"][0]["signals"])
                tool_signals = [signal for signal in data["details"][0]["signals"] if signal["scope"] == "tool"]
                self.assertEqual(len(tool_signals), 3)
                self.assertNotIn("DO_NOT_EXECUTE", json.dumps(data))

    def test_completed_false_requires_grader_evidence_not_tool_keywords(self):
        source, run = self.run_file([failed(status="completed", grading=None)])
        self.trace(source, run, [{"type": "tool", "result": {"error": "AgentTimeoutError"}}])
        self.assertEqual(self.data(source)["records"][0]["category"], "unknown")
        run["attempts"][0]["grading"] = {"success": False}
        self.save(source, run)
        self.assertEqual(self.data(source)["records"][0]["category"], "grader_failure")

    def test_missing_trace_retains_explicit_outcome(self):
        source, run = self.run_file([failed(status="timeout")])
        (source.parent / run["attempts"][0]["trace"]).unlink()
        data = self.data(source)
        self.assertEqual(data["counts"]["failures"], 1)
        self.assertEqual(data["records"][0]["category"], "timeout")
        self.assertIn("evidence_missing", data["records"][0]["warning_codes"])
        self.assertEqual(data["details"][0]["evidence"][0]["status"], "missing")
        self.assertNotIn("sha256", data["details"][0]["evidence"][0])

    def test_malformed_trace_events_are_not_lost_attempts(self):
        source, run = self.run_file([failed(status="runner_error")])
        path = self.trace(source, run, [
            [],
            {"type": "response_item", "payload": {"type": []}},
            {"type": "item.completed", "item": {"type": {}}},
            {"type": "response_item", "payload": None},
            {"type": "error", "error": "ThrottlingException"},
        ])
        with path.open("a") as stream:
            stream.write('not JSON\n{"type":"step","usage":NaN}\n')
        run["attempts"][0]["trace_sha256"] = sha(path)
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["counts"]["attempts"], 1)
        self.assertEqual(data["records"][0]["category"], "throttling")
        evidence = data["details"][0]["evidence"][0]
        self.assertEqual(evidence["status"], "malformed")
        self.assertGreaterEqual(evidence["malformed_events"], 5)
        signal = data["details"][0]["classification"]
        self.assertEqual(signal["source"], str(path))
        self.assertEqual(signal["line"], 5)

    def test_malformed_upstream_keeps_attempt_and_available_other_evidence(self):
        for payload in ("{broken", {"exception_info": []}, {"verifier_result": {"rewards": []}},
                        {"verifier_result": {"rewards": {"reward": True}}},
                        {"agent_result": []}, {"agent_execution": "invalid"}):
            with self.subTest(payload=payload):
                source, run = self.run_file([failed(status="runner_error")])
                self.trace(source, run, payload, suffix=".json")
                data = self.data(source)
                self.assertEqual(data["counts"]["attempts"], 1)
                self.assertEqual(data["records"][0]["category"], "runner_error")
                self.assertIn("evidence_malformed", data["records"][0]["warning_codes"])

    def test_missing_or_malformed_error_grading_shapes_warn(self):
        source, run = self.run_file([failed(status="runner_error", errors={"message": "TimeoutError"},
                                           grading=["not a grader object"])])
        run["attempts"][0]["upstream"] = ["not an upstream object"]
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["counts"]["failures"], 1)
        self.assertTrue({"invalid_errors", "invalid_grading", "evidence_malformed"} <=
                        set(data["records"][0]["warning_codes"]))
        self.assertEqual(data["records"][0]["category"], "runner_error")

    def test_unsafe_paths_are_not_read(self):
        outside = self.directory / "outside.jsonl"
        outside.write_text('{"type":"error","error":"ThrottlingException"}\n')
        for relative in ("../outside.jsonl", str(outside), "https://example.invalid/trace",
                         "C:\\trace.jsonl", "attempts/../../outside.jsonl", "bad\0path"):
            with self.subTest(relative=relative):
                source, _ = self.run_file([failed(status="runner_error", trace=relative)], traces=False)
                data = self.data(source)
                self.assertEqual(data["counts"]["failures"], 1)
                self.assertEqual(data["records"][0]["category"], "runner_error")
                self.assertEqual(data["details"][0]["evidence"][0]["status"], "unsafe")
                self.assertNotIn("sha256", data["details"][0]["evidence"][0])

    def test_symlinks_directories_and_fifos_are_not_evidence(self):
        source, run = self.run_file([failed(status="runner_error")])
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "error.jsonl").write_text('{"type":"error","error":"ThrottlingException"}\n')
        link = source.parent / "linked"
        link.symlink_to(outside, target_is_directory=True)
        direct = source.parent / "direct.jsonl"
        direct.symlink_to(outside / "error.jsonl")
        internal = source.parent / "internal.jsonl"
        internal.symlink_to(source.parent / run["attempts"][0]["trace"])
        directory = source.parent / "a-directory"
        directory.mkdir()
        fifo = source.parent / "a-fifo"
        os.mkfifo(fifo)
        for reference in ("linked/error.jsonl", "direct.jsonl", "internal.jsonl", "a-directory", "a-fifo"):
            with self.subTest(reference=reference):
                run["attempts"][0]["trace"] = reference
                self.save(source, run)
                data = self.data(source)
                self.assertEqual(data["details"][0]["evidence"][0]["status"], "unsafe")
                self.assertEqual(data["records"][0]["category"], "runner_error")

    def test_unsafe_upstream_relative_part_cannot_be_hidden_by_prefix(self):
        source, run = self.run_file([failed(status="runner_error")])
        run["attempts"][0]["upstream"] = {"result": "/outside.json"}
        self.save(source, run)
        data = self.data(source)
        self.assertIn("evidence_unsafe", data["records"][0]["warning_codes"])
        self.assertEqual(data["records"][0]["category"], "runner_error")

    def test_directory_symlink_swap_between_validation_and_open_cannot_escape(self):
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("Descriptor-relative containment is unavailable on this platform")
        source, run = self.run_file([failed(status="runner_error")])
        self.trace(source, run, [{"type": "completed"}])
        protected_parent = source.parent / "attempts"
        outside = self.directory / "outside-tree"
        shutil.copytree(protected_parent, outside)
        (outside / "attempt-1/evidence.jsonl").write_text('{"type":"error","error":"ThrottlingException"}\n')
        original = diagnosis._read_regular
        swapped = False

        def read(path, maximum, *, root=None):
            nonlocal swapped
            if root is not None and not swapped:
                swapped = True
                protected_parent.rename(source.parent / "original-attempts")
                protected_parent.symlink_to(outside, target_is_directory=True)
            return original(path, maximum, root=root)

        with patch.object(diagnosis, "_read_regular", side_effect=read):
            data = self.data(source)
        self.assertTrue(swapped)
        self.assertEqual(data["details"][0]["evidence"][0]["status"], "unsafe")
        self.assertEqual(data["records"][0]["category"], "runner_error")

    def test_message_error_cites_the_field_actually_recorded(self):
        source, run = self.run_file([failed(status="runner_error")])
        path = self.trace(source, run, [{"type": "error", "message": "ThrottlingException"}])
        selected = self.data(source)["details"][0]["classification"]
        self.assertEqual(selected["source"], str(path))
        self.assertEqual(selected["field"], "/message")
        self.assertEqual(selected["line"], 1)

    def test_checksum_mismatch_prevents_signature_use(self):
        source, run = self.run_file([failed(status="runner_error")])
        path = self.trace(source, run, [{"type": "error", "error": "AccessDeniedException"}])
        path.write_text('{"type":"error","error":"ThrottlingException"}\n')
        data = self.data(source)
        evidence = data["details"][0]["evidence"][0]
        self.assertEqual(evidence["status"], "checksum_mismatch")
        self.assertEqual(evidence["sha256"], sha(path))
        self.assertNotEqual(evidence["sha256"], evidence["recorded_sha256"])
        self.assertEqual(data["records"][0]["category"], "runner_error")
        self.assertEqual(data["details"][0]["signals"], [
            {"category": "grader_failure", "signature": "grader_reported_rejection",
             "source": str(source), "field": "/attempts/0/grading/success",
             "confidence": "high", "scope": "grader"},
        ])
        run["attempts"][0]["trace_sha256"] = "bad-checksum"
        self.save(source, run)
        self.assertEqual(self.data(source)["details"][0]["evidence"][0]["status"], "invalid_checksum")

    def test_unknown_and_known_costs_preserve_failed_spend(self):
        source, _ = self.run_file([
            failed(1, cost_usd=None, known_cost_subtotal_usd=.4, accounting_complete=False),
            failed(2, cost_usd=0, known_cost_subtotal_usd=0),
            attempt(3, cost_usd=.2, known_cost_subtotal_usd=.2),
        ])
        counts = self.data(source)["counts"]
        self.assertEqual(counts["known_cost_attempts"], 2)
        self.assertAlmostEqual(counts["known_cost_subtotal_usd"], .6)
        self.assertAlmostEqual(counts["failed_attempt_known_cost_subtotal_usd"], .4)
        self.assertEqual(counts["failed_attempt_unknown_costs"], 1)
        self.assertIsNone(counts["total_cost_usd"])
        self.assertIsNone(counts["failed_attempt_cost_usd"])

    def test_cost_accounting_flags_do_not_turn_partial_cost_into_total(self):
        source, _ = self.run_file([failed(cost_usd=.5, known_cost_subtotal_usd=.5, accounting_complete=False)])
        data = self.data(source)
        self.assertIsNone(data["counts"]["total_cost_usd"])
        self.assertEqual(data["counts"]["known_cost_subtotal_usd"], .5)
        self.assertIn("accounting_inconsistent", data["records"][0]["warning_codes"])
        source, _ = self.run_file([failed(cost_usd=None, known_cost_subtotal_usd=0, accounting_complete=True)])
        self.assertIsNone(self.data(source)["counts"]["total_cost_usd"])

    def test_authentic_review_preserves_selected_source_and_original_audit_hashes(self):
        from bedrock_bench.library import ACCOUNTING_FIELDS, reviewed_source

        self.assertEqual(diagnosis.ACCOUNTING_FIELDS, ACCOUNTING_FIELDS)
        original, run = self.run_file([failed()])
        selected, reviewed, audit = self.reviewed_file(original, run)
        library_source, _, warning = reviewed_source(original)
        self.assertEqual(library_source, selected)
        self.assertIsNone(warning)
        originals = {path: sha(path) for path in (original, selected, audit)}
        result = diagnosis.write_diagnosis(selected, self.directory / "review-diagnosis", limit=0)
        data = json.loads(result.with_suffix(".json").read_text())
        self.assertEqual(data["counts"]["failures"], 1)
        self.assertEqual(data["counts"]["total_cost_usd"], .25)
        self.assertEqual(data["counts"]["failed_attempt_known_cost_subtotal_usd"], .25)
        self.assertEqual(data["records"][0]["source"], str(selected))
        self.assertEqual(data["records"][0]["source_sha256"], sha(selected))
        provenance = data["runs"][0]["accounting_review"]
        self.assertEqual(provenance["status"], "provenance_verified")
        self.assertEqual(provenance["original_path"], str(original))
        self.assertEqual(provenance["original_sha256"], sha(original))
        self.assertEqual(provenance["audit_path"], str(audit))
        self.assertEqual(provenance["audit_sha256"], sha(audit))
        self.assertFalse(provenance["pricing_audited"])
        self.assertEqual(provenance["original_bytes"], original.stat().st_size)
        self.assertEqual(provenance["audit_bytes"], audit.stat().st_size)
        self.assertEqual(data["coverage"]["source_bytes"], sum(path.stat().st_size for path in originals))
        self.assertEqual(originals, {path: sha(path) for path in originals})
        self.assertNotIn("PRIVATE_AUDIT_CONTENT", json.dumps(data) + result.read_text())

    def test_tampered_review_outcomes_settings_and_original_hash_are_rejected(self):
        mutations = [
            lambda run: run["accounting_review"].update(original_sha256="0" * 64),
            lambda run: run["attempts"][0].update(success=True),
            lambda run: run["attempts"][0].update(status="completed"),
            lambda run: run["attempts"][0]["grading"].update(success=True),
            lambda run: run["attempts"][0]["grading"].update(success=0),
            lambda run: run["attempts"][0]["target"].update(model="altered-model"),
            lambda run: run["attempts"][0].update(wall_seconds=100),
            lambda run: run["attempts"][0].update(trace="different-evidence.jsonl"),
            lambda run: run.update(protocol_hash="altered-protocol"),
            lambda run: run["attempts"].reverse(),
            lambda run: run["attempts"].pop(),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                original, run = self.run_file([failed(1), attempt(2)])
                selected, reviewed, _ = self.reviewed_file(original, run)
                original_hash = sha(original)
                mutate(reviewed)
                self.save(selected, reviewed)
                output = self.directory / f"invalid-review-{index}"
                with self.assertRaises(ValueError):
                    diagnosis.write_diagnosis(selected, output)
                self.assertFalse(output.exists())
                self.assertEqual(sha(original), original_hash)

    def test_changed_original_bytes_invalidate_an_explicit_review(self):
        original, run = self.run_file([failed()])
        selected, _, _ = self.reviewed_file(original, run)
        with original.open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            self.data(selected)

    def test_review_requires_metadata_and_both_proof_sources(self):
        for missing in ("metadata", "original", "audit", "malformed_metadata"):
            with self.subTest(missing=missing):
                original, run = self.run_file([failed()])
                selected, reviewed, audit = self.reviewed_file(original, run)
                if missing == "metadata":
                    reviewed.pop("accounting_review")
                    self.save(selected, reviewed)
                elif missing == "malformed_metadata":
                    reviewed["accounting_review"] = []
                    self.save(selected, reviewed)
                elif missing == "original":
                    original.unlink()
                else:
                    audit.unlink()
                with self.assertRaises(ValueError):
                    self.data(selected)

    def test_review_proof_references_cannot_escape_or_follow_symlinks(self):
        for kind in ("absolute_original", "traversal_original", "absolute_audit",
                     "traversal_audit", "symlink_original", "symlink_audit"):
            with self.subTest(kind=kind):
                original, run = self.run_file([failed()])
                selected, reviewed, audit = self.reviewed_file(original, run)
                metadata = reviewed["accounting_review"]
                if kind == "absolute_original":
                    metadata["original_run"] = str(original)
                elif kind == "traversal_original":
                    metadata["original_run"] = "../" + original.name
                elif kind == "absolute_audit":
                    metadata["source"] = str(audit)
                elif kind == "traversal_audit":
                    metadata["source"] = "../" + audit.name
                elif kind == "symlink_original":
                    link = original.parent / "linked-original.json"
                    link.symlink_to(original)
                    metadata["original_run"] = link.name
                else:
                    link = audit.parent / "linked-audit.txt"
                    link.symlink_to(audit)
                    metadata["source"] = link.name
                self.save(selected, reviewed)
                with self.assertRaises(ValueError):
                    self.data(selected)

    def test_review_original_and_audit_reads_share_bounded_source_budget(self):
        original, run = self.run_file([failed()])
        original.write_text(" " * 4_096 + original.read_text())
        selected, _, audit = self.reviewed_file(original, run)
        original_limit = selected.stat().st_size + 1
        self.assertLess(original_limit, original.stat().st_size)
        with patch.object(diagnosis, "MAX_RUN_BYTES", original_limit), self.assertRaises(ValueError):
            self.data(selected)
        with patch.object(diagnosis, "MAX_EVIDENCE_BYTES", audit.stat().st_size - 1), self.assertRaises(ValueError):
            self.data(selected)
        needed = sum(path.stat().st_size for path in (original, selected, audit))
        with patch.object(diagnosis, "MAX_SOURCE_BYTES", needed - 1), self.assertRaises(ValueError):
            self.data(selected)
        with patch.object(diagnosis, "MAX_SOURCE_BYTES", needed):
            self.assertEqual(self.data(selected)["coverage"]["source_bytes"], needed)

    def test_review_selection_never_falls_back_or_automatically_swaps_original(self):
        original, run = self.run_file([failed()])
        selected, reviewed, _ = self.reviewed_file(original, run)
        self.assertEqual(self.data(original)["counts"]["total_cost_usd"], 1)
        self.assertEqual(self.data(selected)["counts"]["total_cost_usd"], .25)
        with self.assertRaises(ValueError):
            diagnosis.diagnosis_data([original, selected])
        reviewed["attempts"][0]["success"] = True
        self.save(selected, reviewed)
        with self.assertRaises(ValueError):
            self.data(selected)
        original_data = self.data(original)
        self.assertEqual(original_data["counts"]["failures"], 1)
        self.assertEqual(original_data["counts"]["total_cost_usd"], 1)
        self.assertEqual(original_data["records"][0]["source"], str(original))
        self.assertIsNone(original_data["runs"][0]["accounting_review"])

    def test_invalid_and_nonfinite_accounting_rejected_before_output(self):
        mutations = [
            {"cost_usd": -1}, {"cost_usd": True}, {"cost_usd": "1"},
            {"cost_usd": float("nan")}, {"cost_usd": float("inf")},
            {"cost_usd": 10 ** 400}, {"known_cost_subtotal_usd": -1},
            {"known_cost_subtotal_usd": float("inf")}, {"known_cost_subtotal_usd": 2},
            {"accounting_complete": 1}, {"cost_basis": "runner_estimate"},
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                source, _ = self.run_file([failed(**mutation)])
                output = self.directory / f"invalid-output-{index}"
                with self.assertRaises(ValueError):
                    diagnosis.write_diagnosis([source], output)
                self.assertFalse(output.exists())
        source, _ = self.run_file([
            failed(1, cost_usd=1e308, known_cost_subtotal_usd=1e308),
            failed(2, cost_usd=1e308, known_cost_subtotal_usd=1e308),
        ])
        with self.assertRaises(ValueError):
            self.data(source)

    def test_strict_json_rejects_overflow_and_duplicate_keys(self):
        source, run = self.run_file()
        source.write_text(json.dumps(run).replace('"cost_usd": 1.0', '"cost_usd": 1e999'))
        with self.assertRaises(ValueError):
            self.data(source)
        source.write_text('{"schema_version":1,"run_id":"one","run_id":"two","attempts":[]}')
        with self.assertRaises(ValueError):
            self.data(source)

    def test_protocol_and_evidence_kind_partitions_are_explicit(self):
        sources = []
        for changes in (
            {"synthetic": True},
            {"synthetic": False, "validation_only": True},
            {"synthetic": False},
            {"synthetic": False, "protocol_hash": "other-protocol"},
            {"synthetic": True, "validation_only": True},
        ):
            source, _ = self.run_file([failed()], **changes)
            sources.append(source)
        source, run = self.run_file([failed()])
        del run["synthetic"]
        self.save(source, run)
        sources.append(source)
        data = diagnosis.diagnosis_data(sources)
        self.assertEqual(len(data["partitions"]), 6)
        self.assertEqual({row["evidence_kind"] for row in data["partitions"]},
                         {"synthetic", "reference", "live", "unknown", "conflicting"})
        self.assertEqual(data["counts"]["failures"], 6)
        for row in data["cells"]:
            self.assertEqual(row["failures"], 1)
            self.assertIn(row["partition_id"], {part["partition_id"] for part in data["partitions"]})
        self.assertIn("conflicting_evidence_kind", {warning["code"] for warning in data["warnings"]})

    def test_mixed_evidence_withholds_combined_accounting_but_retains_partition_costs(self):
        sources = [
            self.run_file([failed(cost_usd=2, known_cost_subtotal_usd=2)], synthetic=True)[0],
            self.run_file([failed(cost_usd=3, known_cost_subtotal_usd=3)], synthetic=False)[0],
            self.run_file([attempt(cost_usd=0, known_cost_subtotal_usd=0)],
                          synthetic=False, validation_only=True)[0],
        ]
        result = diagnosis.write_diagnosis(sources, self.directory / "mixed-diagnosis", limit=1)
        data = json.loads(result.with_suffix(".json").read_text())
        counts = data["counts"]
        self.assertEqual(counts["attempts"], 3)
        self.assertEqual(counts["recorded_attempts"], 3)
        self.assertEqual(counts["successes"], 1)
        self.assertEqual(counts["failures"], 2)
        self.assertEqual(counts["known_cost_attempts"], 3)
        self.assertEqual(counts["unknown_cost_attempts"], 0)
        self.assertEqual(counts["failed_attempt_unknown_costs"], 0)
        for field in ("total_cost_usd", "known_cost_subtotal_usd", "failed_attempt_cost_usd",
                      "failed_attempt_known_cost_subtotal_usd", "cost_coverage",
                      "recorded_cost_coverage", "failed_attempt_cost_coverage"):
            self.assertIsNone(counts[field])
            self.assertIn(field, data["cost_aggregation"]["withheld_fields"])
        self.assertEqual(data["cost_aggregation"]["status"], "withheld")
        self.assertEqual(data["cost_aggregation"]["evidence_kinds"], ["live", "reference", "synthetic"])
        self.assertTrue(data["cost_aggregation"]["reason"])
        self.assertIn("mixed_evidence_costs_withheld", {row["code"] for row in data["warnings"]})
        partitions = {row["evidence_kind"]: row["counts"] for row in data["partitions"]}
        for kind, cost, failed_cost in (("synthetic", 2, 2), ("live", 3, 3), ("reference", 0, 0)):
            self.assertEqual(partitions[kind]["total_cost_usd"], cost)
            self.assertEqual(partitions[kind]["known_cost_subtotal_usd"], cost)
            self.assertEqual(partitions[kind]["failed_attempt_known_cost_subtotal_usd"], failed_cost)
            self.assertEqual(partitions[kind]["cost_coverage"], 1)
            self.assertEqual(partitions[kind]["recorded_cost_coverage"], 1)
        self.assertEqual(sum(row["attempts"] for row in data["cells"]), 3)
        self.assertTrue(all(row["cost_coverage"] == 1 for row in data["by_model"] + data["by_task"]))

    def test_withheld_mixed_costs_are_not_summed_even_transiently(self):
        sources = [
            self.run_file([failed(cost_usd=1e308, known_cost_subtotal_usd=1e308)], synthetic=kind)[0]
            for kind in (True, False)
        ]
        data = diagnosis.diagnosis_data(sources)
        self.assertEqual(data["counts"]["failures"], 2)
        self.assertIsNone(data["counts"]["total_cost_usd"])
        self.assertEqual([part["counts"]["total_cost_usd"] for part in data["partitions"]], [1e308, 1e308])
        json.dumps(data, allow_nan=False)

    def test_one_evidence_kind_retains_cost_inventory_across_protocols(self):
        sources = [
            self.run_file([failed(cost_usd=1.25, known_cost_subtotal_usd=1.25)])[0],
            self.run_file([attempt(cost_usd=2.75, known_cost_subtotal_usd=2.75)],
                          protocol_hash="another-protocol")[0],
        ]
        data = diagnosis.diagnosis_data(sources)
        self.assertEqual(data["cost_aggregation"]["status"], "same_evidence_kind")
        self.assertEqual(data["cost_aggregation"]["withheld_fields"], [])
        self.assertEqual(data["counts"]["total_cost_usd"], 4)
        self.assertEqual(data["counts"]["failed_attempt_cost_usd"], 1.25)
        self.assertEqual(data["counts"]["cost_coverage"], 1)
        self.assertEqual(len(data["partitions"]), 2)

    def test_unknown_protocol_never_combines_distinct_runs(self):
        sources = [self.run_file([failed()], protocol_hash=None)[0] for _ in range(2)]
        data = diagnosis.diagnosis_data(sources)
        self.assertEqual(len(data["partitions"]), 2)
        self.assertEqual(data["counts"]["attempts"], 2)
        self.assertEqual([part["counts"]["attempts"] for part in data["partitions"]], [1, 1])

    def test_model_identity_distinguishes_settings_and_versions(self):
        rows = [failed(1), failed(2, target={**TARGET, "id": "alias"}),
                failed(3, runner_version="other-version"),
                failed(4, target={**TARGET, "reasoning_effort": "low"})]
        source, _ = self.run_file(rows)
        data = self.data(source)
        self.assertEqual(len(data["models"]), 3)
        self.assertEqual(data["records"][0]["model_key"], data["records"][1]["model_key"])
        self.assertEqual(sorted(row["attempts"] for row in data["by_model"]), [1, 1, 2])

    def test_standard_target_routing_arrays_preserve_execution_identity(self):
        from bedrock_bench.config import Target

        demo = asdict(Target(id="fixture", runner="demo", provider="synthetic", model="fixture"))
        routed = asdict(Target(id="routed", runner="native", provider="openrouter",
                               model="fixture", routing=["provider-a", "provider-b"]))
        for target in (demo, routed):
            with self.subTest(routing=target["routing"]):
                source, _ = self.run_file([attempt(target=target)], experiment={
                    "targets": [target], "tasks": ["task-1"], "repetitions": 1,
                })
                data = self.data(source)
                self.assertEqual(data["counts"]["successes"], 1)
                self.assertEqual(data["models"][0]["routing"], target["routing"])
        for routing in ("provider-a", [None], [3], [""]):
            with self.subTest(invalid_routing=routing):
                source, _ = self.run_file([attempt(target={**TARGET, "routing": routing})])
                with self.assertRaises(ValueError):
                    self.data(source)

    def test_duplicate_paths_copies_and_attempts_do_not_inflate_counts(self):
        source, run = self.run_file([failed()])
        run["attempts"].append(copy.deepcopy(run["attempts"][0]))
        self.save(source, run)
        copy_path = self.directory / "same-run.json"
        copy_path.write_text(json.dumps(run, indent=4))
        data = diagnosis.diagnosis_data([source, source.parent, copy_path])
        self.assertEqual(data["counts"]["attempts"], 1)
        self.assertEqual(len(data["duplicate_sources"]), 2)
        self.assertEqual(data["duplicate_sources"][-1]["sha256"], sha(copy_path))
        self.assertIn("duplicate_attempt", data["runs"][0]["warning_codes"])

    def test_conflicting_duplicate_run_or_attempt_is_rejected(self):
        source, run = self.run_file()
        copy_path = self.directory / "conflicting.json"
        changed = copy.deepcopy(run)
        changed["attempts"][0]["success"] = False
        copy_path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            diagnosis.diagnosis_data([source, copy_path])
        run["attempts"].append({**run["attempts"][0], "status": "timeout"})
        self.save(source, run)
        with self.assertRaises(ValueError):
            self.data(source)

    def test_distinct_attempt_ids_in_same_slot_are_retained_and_flagged(self):
        source, run = self.run_file([failed()])
        extra = copy.deepcopy(run["attempts"][0])
        extra["attempt_id"] = "distinct-attempt"
        run["attempts"].append(extra)
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["counts"]["recorded_attempts"], 2)
        self.assertEqual(data["counts"]["missing_attempts"], 0)
        self.assertIn("repeated_slot", data["runs"][0]["warning_codes"])

    def test_grid_and_explicit_schedule_missing_slots_use_real_source_fields(self):
        source, run = self.run_file([attempt()], experiment={
            "targets": [TARGET], "tasks": ["task-1", "task-2"], "repetitions": 2,
        })
        del run["schedule"]
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["counts"]["attempts"], 4)
        self.assertEqual(data["counts"]["missing_attempts"], 3)
        self.assertEqual(data["runs"][0]["expected_attempts"], 4)
        self.assertEqual(data["runs"][0]["schedule_basis"], "experiment_grid")
        missing = [detail for detail in data["details"] if detail["classification"]["scope"] == "schedule"]
        self.assertEqual(len(missing), 3)
        self.assertTrue(all(detail["classification"]["field"] == "/experiment" for detail in missing))
        source, run = self.run_file([], status="interrupted", schedule=[
            {"target": "target-a", "task": "task-a", "repetition": 1, "attempt_id": "scheduled-id"},
        ])
        data = self.data(source)
        self.assertEqual(data["counts"]["missing_attempts"], 1)
        self.assertEqual(data["counts"]["interrupted_attempts"], 0)
        self.assertEqual(data["records"][0]["attempt_id"], "scheduled-id")
        self.assertEqual(data["details"][0]["classification"]["field"], "/schedule/0")

    def test_missing_schedule_or_identity_is_unknown_not_zero_coverage_claim(self):
        source, run = self.run_file()
        del run["schedule"]
        self.save(source, run)
        data = self.data(source)
        self.assertFalse(data["runs"][0]["missing_attempts_known"])
        self.assertIsNone(data["runs"][0]["expected_attempts"])
        self.assertEqual(data["coverage"]["runs_with_unknown_missing_attempts"], 1)
        source, run = self.run_file()
        del run["attempts"][0]["repetition"]
        del run["attempts"][0]["attempt_id"]
        self.save(source, run)
        data = self.data(source)
        self.assertEqual(data["counts"]["recorded_attempts"], 1)
        self.assertEqual(data["counts"]["missing_attempts"], 0)
        self.assertIsNone(data["records"][0]["attempt_id"])
        self.assertFalse(data["runs"][0]["missing_attempts_known"])
        self.assertIn("unreconciled_schedule", data["runs"][0]["warning_codes"])

    def test_conflicting_outcomes_remain_unknown(self):
        source, _ = self.run_file([attempt(status="timeout", success=True)])
        data = self.data(source)
        self.assertEqual(data["counts"]["unknown_outcomes"], 1)
        self.assertEqual(data["counts"]["successes"], 0)
        self.assertEqual(data["counts"]["failures"], 0)
        self.assertEqual(data["records"][0]["category"], "unknown")
        self.assertEqual(data["records"][0]["confidence"], "low")
        source, _ = self.run_file([attempt(grading={"success": False})])
        self.assertEqual(self.data(source)["counts"]["unknown_outcomes"], 1)

    def test_invalid_schema_and_limits_are_rejected(self):
        for mutation in (
            {"schema_version": True}, {"schema_version": 2}, {"attempts": {}},
            {"attempts": [None]}, {"synthetic": "false"}, {"experiment": []},
            {"schedule": [None]}, {"protocol_hash": ["invalid"]},
        ):
            source, _ = self.run_file(**mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.data(source)
        for field, value in (("target", []), ("success", 0), ("repetition", True),
                             ("attempt_id", "a\nb"), ("task_id", "x" * 513)):
            source, run = self.run_file()
            run["attempts"][0][field] = value
            self.save(source, run)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.data(source)
        source, _ = self.run_file()
        for limit in (-1, 1001, True, 1.5, "2", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.data(source, limit=limit)
        empty_details = self.data(source, limit=0)
        self.assertEqual(empty_details["details"], [])
        self.assertEqual(empty_details["counts"]["attempts"], 1)
        for sources in ([], None, [3], {}):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                diagnosis.diagnosis_data(sources)

    def test_source_and_population_limits_do_not_silently_truncate(self):
        source, run = self.run_file()
        with patch.object(diagnosis, "MAX_RUN_BYTES", 50), self.assertRaises(ValueError):
            self.data(source)
        with patch.object(diagnosis, "MAX_SOURCE_BYTES", 50), self.assertRaises(ValueError):
            self.data(source)
        with patch.object(diagnosis, "MAX_SOURCES", 1), self.assertRaises(ValueError):
            diagnosis.diagnosis_data([source, source])
        run["schedule"].append({"target": TARGET["id"], "task": "missing", "repetition": 1})
        self.save(source, run)
        with patch.object(diagnosis, "MAX_RECORDS", 1), self.assertRaises(ValueError):
            self.data(source)

    def test_evidence_file_event_and_total_limits_retain_counts(self):
        source, run = self.run_file([failed(1, status="timeout"), failed(2)])
        with patch.object(diagnosis, "MAX_EVIDENCE_BYTES", 8):
            data = self.data(source)
        self.assertEqual(data["counts"]["attempts"], 2)
        self.assertTrue(all(detail["evidence"][0]["status"] == "too_large" for detail in data["details"]))
        trace_bytes = len((source.parent / run["attempts"][0]["trace"]).read_bytes())
        with patch.object(diagnosis, "MAX_TOTAL_EVIDENCE_BYTES", trace_bytes):
            data = self.data(source)
        self.assertEqual(data["coverage"]["evidence_bytes_read"], trace_bytes)
        self.assertEqual(data["counts"]["failures"], 2)
        self.assertEqual(data["details"][1]["evidence"][0]["status"], "budget_exceeded")
        self.trace(source, run, [
            {"type": "reasoning", "text": "x" * 1_000},
            {"type": "error", "error": "TimeoutError"},
        ])
        with patch.object(diagnosis, "MAX_EVENT_BYTES", 100), patch.object(diagnosis, "MAX_EVENTS", 1):
            data = self.data(source)
        self.assertEqual(data["counts"]["attempts"], 2)
        self.assertIn("evidence_malformed", data["records"][0]["warning_codes"])
        self.assertEqual(data["details"][0]["evidence"][0]["malformed_events"], 2)

    def test_error_and_signal_limits_bound_projected_details(self):
        errors = ["AccessDeniedException " + "x" * 5_000] * 100
        source, _ = self.run_file([failed(status="runner_error", errors=errors)])
        data = self.data(source)
        self.assertLessEqual(len(data["details"][0]["signals"]), diagnosis.MAX_SIGNALS)
        self.assertIn("error_limit", data["records"][0]["warning_codes"])
        self.assertEqual(data["records"][0]["category"], "authentication")
        self.assertLess(len(json.dumps(data["details"])), 20_000)

    def test_private_trace_fields_raw_errors_and_credentials_are_not_projected(self):
        secret = "fixture-secret-known-to-environment"
        source, run = self.run_file([failed(
            status="runner_error",
            errors=[f"TimeoutError; PRIVATE_ERROR_TEXT; token={secret}; sk-test_abcdefghijklmnopqrstuvwxyz"],
            final_text="PRIVATE_FINAL_TEXT",
            target={**TARGET, "model": f"model-{secret}"},
        )])
        run["protocol"] = {"system_prompt": "PRIVATE_SYSTEM_PROMPT"}
        run["runtime"] = {"credentials": "PRIVATE_RUNTIME_CREDENTIALS"}
        self.trace(source, run, [
            {"type": "session_meta", "payload": {"instructions": "PRIVATE_CONFIGURATION"}},
            {"type": "response_item", "payload": {"type": "reasoning", "text": "PRIVATE_REASONING"}},
            {"type": "response_item", "payload": {"type": "message", "role": "system",
             "content": [{"type": "input_text", "text": "PRIVATE_SYSTEM_MESSAGE"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "arguments": "PRIVATE_COMMAND"}},
            {"type": "response_item", "payload": {"type": "function_call_output",
             "output": "PRIVATE_TOOL_OUTPUT " + secret}},
            {"type": "error", "error": {"message": "TimeoutError", "stack": "PRIVATE_STACK",
                                      "system_prompt": "PRIVATE_NESTED_PROMPT"}},
        ])
        with patch.dict(os.environ, {"DIAGNOSIS_TEST_TOKEN": secret}):
            path = diagnosis.write_diagnosis([source], self.directory / "safe-output")
        text = path.read_text() + path.with_suffix(".json").read_text()
        self.assertNotIn("PRIVATE_", text)
        self.assertNotIn(secret, text)
        self.assertNotIn("sk-test_abcdefghijklmnopqrstuvwxyz", text)
        data = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(data["records"][0]["category"], "timeout")
        self.assertIn("[REDACTED]", data["records"][0]["model"])
        self.assertEqual(data["records"][0]["source_sha256"], sha(source))

    def test_markdown_labels_are_inert(self):
        source, _ = self.run_file([failed(task_id='<script>alert(1)</script>[x](https://example.invalid)|`')])
        path = diagnosis.write_diagnosis(source, self.directory / "inert-output")
        text = path.read_text()
        self.assertNotIn("<script>", text)
        self.assertNotIn("[x](https://example.invalid)", text)
        data = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(data["counts"]["failures"], 1)
        self.assertIn("<script>", data["records"][0]["task_id"])

    def test_writer_contract_is_portable_finite_and_nonoverwriting(self):
        source, _ = self.run_file([failed()])
        before = {path: sha(path) for path in source.parent.rglob("*") if path.is_file()}
        output = self.directory / "output"
        path = diagnosis.write_diagnosis(source.parent, output)
        self.assertIsInstance(path, Path)
        self.assertEqual(path, output / "DIAGNOSIS.md")
        self.assertTrue(path.is_absolute())
        data = json.loads((output / "DIAGNOSIS.json").read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["artifact"], "bedrock-bench-diagnosis")
        json.dumps(data, allow_nan=False)
        after = {path: sha(path) for path in source.parent.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        report_hashes = {path.name: sha(path) for path in output.iterdir()}
        with self.assertRaises(ValueError):
            diagnosis.write_diagnosis(source, output)
        self.assertEqual(report_hashes, {path.name: sha(path) for path in output.iterdir()})

    def test_source_evidence_hardlinks_and_symlinks_cannot_be_overwritten(self):
        source, run = self.run_file([failed()])
        original_hash = sha(source)
        # A source may have an arbitrary name, including the output basename.
        named_source = source.parent / "DIAGNOSIS.json"
        named_source.write_bytes(source.read_bytes())
        with self.assertRaises(ValueError):
            diagnosis.write_diagnosis(named_source, source.parent)
        self.assertEqual(sha(named_source), original_hash)
        self.assertFalse((source.parent / "DIAGNOSIS.md").exists())
        for link_type in ("symlink", "hardlink"):
            output = self.directory / link_type
            output.mkdir()
            target = output / "DIAGNOSIS.md"
            if link_type == "symlink":
                target.symlink_to(source)
            else:
                os.link(source, target)
            with self.assertRaises(ValueError):
                diagnosis.write_diagnosis(source, output)
            self.assertEqual(sha(source), original_hash)
            self.assertFalse((output / "DIAGNOSIS.json").exists())
        evidence = source.parent / "DIAGNOSIS.md"
        evidence.write_text("saved public artifact")
        before = sha(evidence)
        with self.assertRaises(ValueError):
            diagnosis.write_diagnosis(source, source.parent)
        self.assertEqual(sha(evidence), before)

    def test_missing_evidence_destination_is_protected_even_when_details_are_disabled(self):
        source, _ = self.run_file([failed(trace="DIAGNOSIS.json")], traces=False)
        with self.assertRaises(ValueError):
            diagnosis.write_diagnosis(source, source.parent, limit=0)
        self.assertFalse((source.parent / "DIAGNOSIS.json").exists())
        self.assertFalse((source.parent / "DIAGNOSIS.md").exists())

    def test_module_never_executes_recorded_commands_or_opens_network(self):
        source, run = self.run_file([failed()])
        run["attempts"][0]["command"] = ["DO_NOT_EXECUTE_RECORDED_COMMAND"]
        self.save(source, run)
        with patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("os.system", side_effect=AssertionError("shell forbidden")):
            path = diagnosis.write_diagnosis(source, self.directory / "offline")
        self.assertTrue(path.is_file())

    def test_fixture_is_deterministic_synthetic_and_exercises_missing_attempts(self):
        spec = importlib.util.spec_from_file_location("diagnosis_fixture", SKILL / "scripts/make_fixture.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        first = fixture.make_fixture(self.directory / "fixture-one")
        second = fixture.make_fixture(self.directory / "fixture-two")
        self.assertEqual([sha(path) for path in first], [sha(path) for path in second])
        data = diagnosis.diagnosis_data(first, limit=2)
        self.assertEqual({part["evidence_kind"] for part in data["partitions"]}, {"synthetic"})
        self.assertEqual(len(data["partitions"]), 2)
        self.assertEqual(data["counts"]["recorded_attempts"], 12)
        self.assertEqual(data["counts"]["successes"], 4)
        self.assertEqual(data["counts"]["failures"], 6)
        self.assertEqual(data["counts"]["interrupted_attempts"], 2)
        self.assertEqual(data["counts"]["missing_attempts"], 4)
        self.assertAlmostEqual(data["counts"]["failed_attempt_known_cost_subtotal_usd"], 1.2)
        self.assertIsNone(data["counts"]["total_cost_usd"])
        with self.assertRaises(FileExistsError):
            fixture.make_fixture(self.directory / "fixture-one")

    def test_installed_skill_helpers_work_without_neighboring_checkout(self):
        installed = self.directory / "installed-plugin"
        package = installed / "scripts/bedrock_bench"
        package.mkdir(parents=True)
        for name in ("__init__.py", "diagnosis.py"):
            shutil.copy2(PLUGIN / "scripts/bedrock_bench" / name, package / name)
        skill = installed / "skills/diagnose-failures"
        shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
        unrelated = self.directory / "unrelated-working-directory"
        unrelated.mkdir()
        fixture = subprocess.run(
            [sys.executable, "-I", "-B", str(skill / "scripts/make_fixture.py"),
             "--out", str(self.directory / "installed-fixture")],
            cwd=unrelated, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(fixture.returncode, 0, fixture.stderr)
        sources = fixture.stdout.splitlines()
        self.assertEqual(len(sources), 2)
        process = subprocess.run(
            [sys.executable, "-I", "-B", str(skill / "scripts/diagnose.py"), *sources,
             "--out", str(self.directory / "installed-output"), "--limit", "1"],
            cwd=unrelated, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        path = Path(process.stdout.strip())
        self.assertEqual(path.name, "DIAGNOSIS.md")
        data = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(data["counts"]["attempts"], 16)
        self.assertEqual(len(data["details"]), 1)
        self.assertFalse((installed / "bench.py").exists())
        self.assertFalse(any(installed.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
