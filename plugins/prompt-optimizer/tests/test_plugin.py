"""Exercise an installed plugin from outside its repository; no live services."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]


class InstalledPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.TemporaryDirectory(prefix="workflow-plugin-cache-")
        cls.installed = Path(cls.cache.name) / "cache with spaces" / "prompt-optimizer"
        shutil.copytree(PLUGIN, cls.installed, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    @classmethod
    def tearDownClass(cls):
        cls.cache.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="invoice-user-workspace-")
        self.workspace = Path(self.work.name)
        self.addCleanup(self.work.cleanup)

    def cli(self, *args, ok=True):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", AWS_EC2_METADATA_DISABLED="true")
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", str(self.installed / "scripts/workflow_eval.py"), *map(str, args)],
            cwd=self.workspace, env=env, capture_output=True, text=True,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def prepare_study(self):
        import yaml

        source = self.workspace / "invoices.jsonl"
        source.write_text("\n".join(json.dumps({
            "id": f"invoice-{i}", "prompt": "Extract the printed invoice total as JSON.",
            "input": f"Invoice {i}: total {i}.00", "expected": {"total": f"{i}.00"},
        }) for i in range(24)))
        project = self.workspace / "study"
        start = self.cli("start", "--project", project)
        self.assertFalse(start["decisions"]["optimization_enabled"])
        self.assertFalse(any("generate prompt revisions" in q for q in start["missing_decisions"]))
        self.cli("inspect", "--project", project, "--input", source)
        example = yaml.safe_load((self.installed / "engine/examples/log-replay/input.yaml").read_text())
        connection = {
            "version": 1,
            "models": {
                m["id"]: {"model": m, "route": example["runner"]["routes"][m["id"]],
                          "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"}
                for m in example["models"] if m["id"] in ("m1", "m2")
            },
            "cloud": {"runtime_arn": None, "profile": "test", "region": "us-east-1",
                      "account": "000000000000", "bucket": None},
        }
        path = self.workspace / "connection.json"
        path.write_text(json.dumps(connection))
        answers = self.workspace / "answers.json"
        answers.write_text(json.dumps({
            "owner": "Fixture test", "source_model": "m1", "target_model": "m2",
            "connection": str(path), "prompt_location": 0, "provenance": "synthetic",
            "budget_policy": "capped", "budget_usd": 10,
            "quality": {"kind": "json_equals", "reference_source": "supplied"},
        }))
        configured = self.cli("configure", "--project", project, "--decisions", answers)
        self.assertTrue(configured["ready_to_prepare"], configured)
        prepared = self.cli("prepare", "--project", project)
        self.assertIn("fingerprint", prepared)
        return project

    def test_portable_manifest_and_single_public_skill(self):
        native = json.loads((self.installed / "plugin.json").read_text())
        codex = json.loads((self.installed / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(native["name"], codex["name"])
        self.assertEqual(native["version"], codex["version"])
        self.assertEqual(codex["skills"], "./skills/")
        self.assertEqual([p.parent.name for p in (self.installed / "skills").glob("*/SKILL.md")], ["evaluate-workflows"])
        self.assertTrue((self.installed / "LICENSE").is_file())

    def test_repository_marketplace_only_references_available_plugins(self):
        repository = PLUGIN.parents[1]
        marketplace = json.loads((repository / '.agents/plugins/marketplace.json').read_text())
        self.assertTrue(marketplace['plugins'])
        for entry in marketplace['plugins']:
            path = (repository / entry['source']['path']).resolve()
            self.assertTrue(path.is_relative_to(repository.resolve()))
            manifest = json.loads((path / 'plugin.json').read_text())
            self.assertEqual(entry['name'], manifest['name'])

    def test_recorded_demo_is_self_contained_and_needs_no_dependencies(self):
        destination = self.workspace / "report"
        # -S omits site-packages: the demo must use only the standard library.
        result = subprocess.run(
            [sys.executable, "-S", str(self.installed / "scripts/workflow_eval.py"),
             "demo", "--output", str(destination)],
            cwd=self.workspace, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["fresh_model_calls"], 0)
        self.assertEqual(response["status"], "recorded_report_ready")
        self.assertTrue((destination / "architecture.drawio.png").is_file())
        self.assertTrue((destination / "local/decision.html").is_file())
        self.assertTrue((destination / "agentcore/decision.html").is_file())
        evidence = json.loads((destination / "comparison.json").read_text())
        self.assertEqual(evidence["evaluation_trials"], 192)
        self.assertFalse(evidence["infrastructure_included"])
        before = (destination / "index.html").read_bytes()
        self.cli("demo", "--output", destination, ok=False)
        self.assertEqual((destination / "index.html").read_bytes(), before)

    def test_plugin_cache_and_symlink_outputs_are_rejected(self):
        for action, flag in (("demo", "--output"), ("start", "--project")):
            result = self.cli(action, flag, self.installed / "user-data", ok=False)
            self.assertIn("outside the installed plugin", result.stderr)
        link = self.workspace / "cache-link"
        link.symlink_to(self.installed, target_is_directory=True)
        self.cli("demo", "--output", link / "user-data", ok=False)
        self.assertFalse((self.installed / "user-data").exists())

    def test_fixed_prompt_prepare_has_only_two_routes_and_is_immutable(self):
        import yaml

        project = self.prepare_study()
        config = yaml.safe_load((project / "prepared/input.yaml").read_text())
        self.assertFalse(config["optimization"]["enabled"])
        self.assertIsNone(config["runner"]["optimizer_model_id"])
        self.assertEqual(set(config["runner"]["routes"]), {"m1", "m2"})
        self.assertEqual(len(config["models"]), 2)
        self.assertTrue(all(d["label_status"] == "unreviewed" for d in config["datasets"]))
        plan = self.cli("plan", "--project", project)
        self.assertEqual(plan["fixed_prompt_evaluation_trials_per_backend"], 144)
        self.assertEqual(plan["calls_made"], 0)
        self.assertFalse(plan["prompt_optimization"])
        (project / "prepared/prompt.txt").write_text("Changed after preparation")
        result = self.cli("plan", "--project", project, ok=False)
        self.assertEqual(json.loads(result.stderr)["error_type"], "ValueError")
        self.assertFalse((project / "local").exists())

    def test_preflight_blockers_have_nonzero_exit_and_keep_diagnostic_json(self):
        project = self.prepare_study()
        before = (project / 'project.json').read_bytes()
        # This fixture has no cloud runtime/bucket: fail before any AWS calls.
        result = self.cli('preflight', '--project', project, '--backend', 'agentcore', ok=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertFalse(report['ready_for_qualification'])
        self.assertIn('cloud_connection', report['blockers'])
        self.assertTrue(report['read_only'])
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual((project / 'project.json').read_bytes(), before)
        self.assertFalse((project / 'submission').exists())

    def test_reopening_an_older_intake_preserves_optimization_intent(self):
        project = self.workspace / "legacy"
        project.mkdir()
        state = {"version": 1, "goal": "Improve my prompt", "stage": "intake",
                 "decisions": {"optimizer_model": "optimizer"}, "import_revision": 0}
        (project / "project.json").write_text(json.dumps(state))
        self.cli("start", "--project", project)
        self.assertEqual(json.loads((project / "project.json").read_text()), state)

    def test_live_execution_requires_explicit_flag(self):
        for backend in ("local", "agentcore"):
            result = self.cli("run", "--project", self.workspace / "study", "--backend", backend, ok=False)
            self.assertIn("--live", result.stderr)
        self.assertFalse((self.workspace / "study").exists())

    def test_unsupported_python_fails_before_engine_dependencies(self):
        spec = importlib.util.spec_from_file_location(
            "fresh_setup_entrypoint", self.installed / "scripts/workflow_eval.py")
        entrypoint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entrypoint)
        project = self.workspace / "unsupported-python-study"
        stderr = io.StringIO()
        # An attempted engine import would fail with ModuleNotFoundError instead
        # of the actionable interpreter message, even if this test's Python has
        # every engine dependency installed.
        with patch.object(sys, "version_info", (3, 9, 6)), \
                patch.dict(sys.modules, {"customer_eval": None}), \
                patch.object(sys, "stderr", stderr):
            result = entrypoint.main(["start", "--project", str(project)])
        self.assertEqual(result, 2)
        response = json.loads(stderr.getvalue())
        self.assertEqual(response["error_type"], "ValueError")
        self.assertIn("require Python 3.12", response["message"])
        self.assertIn("Python 3.9.6", response["message"])
        self.assertIn(str(self.installed / "requirements.txt"), response["message"])
        self.assertFalse(project.exists())

    def test_relative_decision_paths_survive_resuming_from_another_directory(self):
        project = self.workspace / 'relative-study'
        self.cli('start', '--project', project)
        answers_dir = self.workspace / 'answers'
        answers_dir.mkdir()
        answers = answers_dir / 'choices.json'
        answers.write_text(json.dumps({'connection': 'connection.json',
            'label_review_file': 'review.json', 'quality': {'reference_file': 'labels.json'}}))
        # CLI runs in workspace, while references are relative to the answer file.
        configured = self.cli('configure', '--project', project, '--decisions', answers)
        self.assertEqual(configured['decisions']['connection'], str((answers_dir / 'connection.json').resolve()))
        self.assertEqual(configured['decisions']['label_review_file'], str((answers_dir / 'review.json').resolve()))
        self.assertEqual(configured['decisions']['quality']['reference_file'], str((answers_dir / 'labels.json').resolve()))
        result = subprocess.run([sys.executable, str(self.installed / 'scripts/workflow_eval.py'),
            'status', '--project', str(project)], cwd=answers_dir, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Local status reports no completed run, and preserves the saved absolute paths.
        state = json.loads((project / 'project.json').read_text())
        self.assertEqual(state['decisions']['connection'], str((answers_dir / 'connection.json').resolve()))

    def test_explicit_optimize_mode_and_reopen_preserve_intent(self):
        project = self.workspace / 'optimize-study'
        value = self.cli('start', '--project', project, '--mode', 'optimize')
        self.assertTrue(value['decisions']['optimization_enabled'])
        self.assertTrue(any('generate prompt revisions' in q for q in value['missing_decisions']))
        self.cli('start', '--project', project)
        self.assertTrue(json.loads((project / 'project.json').read_text())['decisions']['optimization_enabled'])
        self.cli('start', '--project', project, '--mode', 'compare', ok=False)

    def test_uncertain_cloud_submission_is_not_replayed(self):
        project = self.workspace / "study"
        (project / "submission").mkdir(parents=True)
        receipt = project / "submission/job.json"
        receipt.write_text('{"status":"uncertain"}')
        result = self.cli("run", "--project", project, "--backend", "agentcore", "--live")
        self.assertEqual(result["status"], "already_submitted")
        self.assertEqual(receipt.read_text(), '{"status":"uncertain"}')
        self.assertEqual(len(list((project / "submission").glob("*.json"))), 1)

    def test_local_dispatch_preserves_frozen_optimization_choice(self):
        project_dir = self.prepare_study()
        spec = importlib.util.spec_from_file_location("workflow_plugin_cli", self.installed / "scripts/workflow_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.path.insert(0, str(self.installed / "engine"))
        try:
            from customer_eval import project
            with patch("customer_eval.runner.Runner") as runner:
                module.run_local(project, project_dir)
                self.assertEqual(runner.call_args.kwargs["backend"], "local")
                self.assertFalse(runner.call_args.args[0]["document"]["optimization"]["enabled"])
                runner.return_value.run.assert_called_once_with(optimize=False)
                runner.return_value.close.assert_called_once()
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
