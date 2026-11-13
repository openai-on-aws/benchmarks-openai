"""Portable dashboard evidence semantics; no model or AWS dependencies."""
import importlib.util
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prompt_dashboard", PLUGIN / "scripts/dashboard.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


def report(backend="local", fingerprint="same-inputs", simulated=False):
    group = {
        "score": {"accuracy": 0.5, "cost_usd_per_session": None, "n_sessions": 2},
        "trials": [{
            "id": "attempt-1", "case_id": "case-1", "repeat": 0, "model_id": "source",
            "result": {
                "status": "completed", "outputs": ['</script><script>alert("unsafe")</script>'],
                "turn_records": [{"user": "Read this input", "output": "Saved answer"}],
                "route_calls": [{"model_requested": "configured-source", "usage": {"output_tokens": 10}}],
                "timing": {"conversation_ms": 1200},
            },
            "checks": {"optional_check": {"pass": False, "reason": "Human review required"}},
        }],
    }
    return {
        "backend": backend, "fingerprint": fingerprint, "simulated": simulated,
        "evaluation_status": "complete", "holdout": {
            "source_original": group,
            "target_original": {"score": {"accuracy": 1, "cost_usd_per_session": 0}, "trials": []},
            "target_selected": dict(group, reused_from="target_original"),
        },
        "model_costs": {"estimated_model_cost_usd": None},
    }


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="prompt-dashboard-test-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def save(self, name, value):
        path = self.root / name / "report.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
        return path

    def test_saved_evidence_keeps_unknowns_and_skips_reused_groups(self):
        self.save("local", report())
        data = dashboard.build_data(self.root)
        run = data["reports"][0]
        self.assertFalse(data["historical"])
        self.assertIn("unavailable", data["provenance"])
        self.assertEqual(len(run["groups"]), 2)
        group = run["groups"][0]
        self.assertEqual(group["model"], "configured-source")
        self.assertEqual(group["score"]["accuracy"], 0.5)
        self.assertIsNone(group["score"]["cost_usd_per_session"])
        self.assertEqual(run["groups"][1]["score"]["cost_usd_per_session"], 0)
        self.assertIsNone(group["trials"][0]["cost_usd"])
        self.assertEqual(group["trials"][0]["turn_records"][0]["user"], "Read this input")
        self.assertFalse(group["trials"][0]["checks"]["optional_check"]["pass"])

    def test_incompatible_evidence_cannot_be_combined(self):
        self.save("local", report())
        cloud = self.save("agentcore", report("agentcore", "other-inputs"))
        with self.assertRaisesRegex(ValueError, "fingerprints"):
            dashboard.build_data(self.root)
        cloud.write_text(json.dumps(report("agentcore", None)))
        with self.assertRaisesRegex(ValueError, "fingerprints"):
            dashboard.build_data(self.root)
        cloud.write_text(json.dumps(report("agentcore", simulated=True)))
        with self.assertRaisesRegex(ValueError, "Simulated and live"):
            dashboard.build_data(self.root)
        cloud.write_text(json.dumps(report("local")))
        with self.assertRaisesRegex(ValueError, "same backend"):
            dashboard.build_data(self.root)

    def test_single_partial_report_remains_inspectable(self):
        value = report(fingerprint=None)
        value["holdout"] = {}
        value["evaluation_status"] = "paused"
        value["unresolved_attempts"] = [{"trial_id": "pending"}]
        self.save("results", value)
        data = dashboard.build_data(self.root)
        self.assertIsNone(data["reports"][0]["fingerprint"])
        self.assertEqual(data["reports"][0]["status"], "paused")
        self.assertEqual(data["reports"][0]["groups"], [])
        self.assertEqual(len(data["reports"][0]["unresolved_attempts"]), 1)

    def test_missing_integrity_counts_and_unfinished_groups_stay_unknown(self):
        value = report()
        value["holdout"]["target_original"] = {}
        self.save("local", value)
        run = dashboard.build_data(self.root)["reports"][0]
        self.assertIsNone(run["unresolved_attempts"])
        self.assertIsNone(run["grading_error_records"])
        self.assertEqual(len(run["groups"]), 2)
        self.assertEqual(run["groups"][1]["id"], "target_original")
        self.assertEqual(run["groups"][1]["score"], {})
        self.assertEqual(run["groups"][1]["trials"], [])
        # A ledger explicitly recording no unresolved attempts remains zero.
        value["unresolved_attempts"] = []
        value["grading_error_records"] = 0
        value["holdout"]["target_original"] = None
        (self.root / "local/report.json").write_text(json.dumps(value))
        run = dashboard.build_data(self.root)["reports"][0]
        self.assertEqual(run["unresolved_attempts"], [])
        self.assertEqual(run["grading_error_records"], 0)
        self.assertEqual(run["groups"][1]["score"], {})

    def test_frozen_dataset_provenance_requires_matching_identity(self):
        config = {"document": {"datasets": [
            {"provenance": "customer", "label_status": "unreviewed"}]}}
        config["fingerprint"] = hashlib.sha256(json.dumps(
            config, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode("utf-8")).hexdigest()
        saved = self.save("local", report(fingerprint=config["fingerprint"]))
        (saved.parent / "run-input.json").write_text(json.dumps(config))
        self.assertIn("customer inputs", dashboard.build_data(self.root)["provenance"])
        # Changing label declarations without changing the recorded input hash
        # must not promote old results to independently reviewed evidence.
        config["document"]["datasets"][0]["label_status"] = "reviewed"
        (saved.parent / "run-input.json").write_text(json.dumps(config))
        self.assertIn("unavailable", dashboard.build_data(self.root)["provenance"])
        # A partially written optional manifest must not hide valid saved results.
        (saved.parent / "run-input.json").write_text('{"document":')
        data = dashboard.build_data(self.root)
        self.assertIn("could not be verified", data["provenance"])
        self.assertEqual(data["reports"][0]["groups"][0]["score"]["accuracy"], 0.5)

    def test_installed_cli_is_offline_and_safely_embeds_report_text(self):
        installed = self.root / "cache with spaces" / "prompt-optimizer"
        shutil.copytree(PLUGIN / "scripts", installed / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        evidence = self.save("workspace/local", report())
        output = self.root / "workspace/dashboard.html"
        command = [sys.executable, "-S", str(installed / "scripts/invoice_eval.py"),
                   "dashboard", "--project", str(evidence.parent.parent), "--output", str(output)]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["fresh_model_calls"], 0)
        self.assertEqual(response["fresh_aws_calls"], 0)
        html = output.read_text()
        # Fonts must travel with a copied, offline HTML, including their notices.
        self.assertNotIn(dashboard.FONT_PLACEHOLDER, html)
        for name in ("open-sans-regular.woff2", "open-sans-bold.woff2"):
            encoded = base64.b64encode((PLUGIN / "scripts/fonts" / name).read_bytes()).decode()
            self.assertIn("data:font/woff2;base64," + encoded, html)
        self.assertIn("Apache License", html)
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", html)
        self.assertIn("Open Sans Fonts", html)
        self.assertNotIn('</script><script>alert("unsafe")</script>', html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertNotIn(dashboard.PLACEHOLDER, html)
        again = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(output.read_text(), html)
        forced = subprocess.run(command + ["--force"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        command[-1] = str(installed / "dashboard.html")
        blocked = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("outside the installed plugin", blocked.stderr)
        self.assertFalse((installed / "dashboard.html").exists())

    def test_recorded_demo_data_is_identified_by_exact_evidence(self):
        source = PLUGIN / "examples/recorded-invoice-comparison"
        for backend in ("local", "agentcore"):
            self.save(backend, json.loads((source / backend / "report.json").read_text()))
            # Preserve bytes, as a copied demo does.
            shutil.copyfile(source / backend / "report.json", self.root / backend / "report.json")
        data = dashboard.build_data(self.root)
        self.assertTrue(data["historical"])
        self.assertIn("Synthetic", data["provenance"])
        self.assertEqual(sum(len(g["trials"]) for r in data["reports"] for g in r["groups"]), 96)
        cloud = self.root / "agentcore/report.json"
        changed = json.loads(cloud.read_text())
        changed["evaluation_status"] = "paused"
        cloud.write_text(json.dumps(changed))
        self.assertFalse(dashboard.build_data(self.root)["historical"])


if __name__ == "__main__":
    unittest.main()
