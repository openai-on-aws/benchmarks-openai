"""Exercise research workflows through the independently installed plugin."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bedrock-bench"


class ResearchCliTests(unittest.TestCase):
    def test_shipped_brief_plans_and_executes_as_a_synthetic_installed_plugin_study(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            package = directory / "installed-plugin"
            project = directory / "research-project"
            project.mkdir()
            shutil.copytree(PLUGIN, package, ignore=shutil.ignore_patterns("__pycache__"))
            cli = package / "scripts/bench.py"
            brief = package / "skills/design-experiment/assets/offline-demo.brief.json"
            before = hashlib.sha256(brief.read_bytes()).hexdigest()

            def invoke(*args):
                process = subprocess.run(
                    [sys.executable, "-B", str(cli), *map(str, args)],
                    cwd=project, capture_output=True, text=True, timeout=45,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                return process.stdout.strip()

            design_path = Path(invoke("design", brief, "--out", "study"))
            self.assertEqual(design_path, project / "study/DESIGN.md")
            design = json.loads(design_path.with_name("DESIGN.json").read_text())
            config = design_path.with_name("experiment.json")
            plan = json.loads(invoke("plan", config))
            self.assertEqual(plan["mode"], "synthetic")
            self.assertEqual(plan["attempts"], 12)
            self.assertEqual(design["counts"]["total_attempts"], plan["attempts"])
            report = Path(invoke("run", config, "--execute", "--out", "study-runs"))
            run = json.loads(report.with_name("run.json").read_text())
            self.assertTrue(run["synthetic"])
            self.assertEqual(len(run["attempts"]), plan["attempts"])
            self.assertEqual(sum(row["success"] for row in run["attempts"]), 10)
            self.assertEqual(run["protocol_hash"], plan["protocol_hash"])
            self.assertEqual(hashlib.sha256(brief.read_bytes()).hexdigest(), before)
            self.assertFalse((project / ".bench-tools").exists())

    def test_installed_plugin_compares_diagnoses_and_audits_without_changing_saved_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            package = directory / "installed-plugin"
            project = directory / "research-project"
            project.mkdir()
            shutil.copytree(PLUGIN, package, ignore=shutil.ignore_patterns("__pycache__"))
            cli = package / "scripts/bench.py"

            def invoke(*args):
                process = subprocess.run(
                    [sys.executable, "-B", str(cli), *map(str, args)],
                    cwd=project, capture_output=True, text=True, timeout=45,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                artifact = Path(process.stdout.strip())
                self.assertTrue(artifact.is_file(), process.stdout)
                self.assertTrue(artifact.resolve().is_relative_to(project))
                return artifact

            report = invoke("demo", "--out", "runs")
            source = report.parent / "run.json"
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            invoke(
                "compare-experiments", source, "--baseline", "fixture-efficient",
                "--candidate", "fixture-imperfect", "--resamples", "200", "--out", "comparison",
            )
            comparison = json.loads((project / "comparison/COMPARISON.json").read_text())
            self.assertEqual(comparison["schema_version"], 1)
            second_report = invoke("demo", "--out", "runs")
            second_source = second_report.parent / "run.json"
            first_id = json.loads(source.read_text())["run_id"]
            second_id = json.loads(second_source.read_text())["run_id"]
            invoke(
                "compare-experiments", source, second_source, "--baseline", "fixture-efficient",
                "--candidate", "fixture-efficient", "--baseline-run", first_id,
                "--candidate-run", second_id, "--resamples", "200", "--out", "history-comparison",
            )
            history = json.loads((project / "history-comparison/COMPARISON.json").read_text())
            self.assertEqual(history["metrics"]["success_rate"]["delta"], 0)
            self.assertEqual(history["selections"]["baseline"]["run_ids"], [first_id])
            self.assertEqual(history["selections"]["candidate"]["run_ids"], [second_id])
            invoke("diagnose", source, "--out", "diagnosis")
            diagnosis = json.loads((project / "diagnosis/DIAGNOSIS.json").read_text())
            self.assertEqual(diagnosis["schema_version"], 1)
            invoke("audit", "--suite", "starter", "--out", "audit-plan")
            self.assertEqual(json.loads((project / "audit-plan/AUDIT.json").read_text())["status"], "planned")
            invoke("audit", "--suite", "starter", "--execute", "--out", "audit")
            self.assertEqual(json.loads((project / "audit/AUDIT.json").read_text())["status"], "passed")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            self.assertFalse((project / ".bench-tools").exists())


if __name__ == "__main__":
    unittest.main()
