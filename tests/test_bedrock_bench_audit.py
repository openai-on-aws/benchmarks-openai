"""Grader-audit behavior, using real starter graders and offline Node controls."""

import hashlib
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
sys.path.insert(0, str(PLUGIN / "scripts"))
from bedrock_bench import audit, tasks


class AuditTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bench-audit-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.tools = self.root / "tools"

    def run_audit(self, name="audit", suite="starter", **kwargs):
        path = audit.write_audit(suite, self.root / name, tools_dir=self.tools, **kwargs)
        return path.parent, json.loads(path.with_name("AUDIT.json").read_text())


class StarterAuditTests(AuditTestCase):
    def test_plan_materializes_exact_controls_without_grading_or_processes(self):
        with patch.object(tasks.Task, "grade", side_effect=AssertionError("No grading in a plan")), \
                patch("subprocess.Popen", side_effect=AssertionError("No subprocesses in a plan")):
            root, result = self.run_audit()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["scope"]["tasks"], list(tasks.TASK_IDS))
        self.assertEqual({c["observed"] for c in result["cases"]}, {"not_run"})
        self.assertGreater(len(result["cases"]), 30)
        self.assertEqual(result["model"], None)
        self.assertTrue(result["synthetic"])
        self.assertIn("--execute", result["plan"]["command"])
        self.assertNotEqual(Path(result["plan"]["command"][-2]), root)
        self.assertEqual({c["label"] for c in result["cases"]}, {"reference", "synthetic"})
        for case in result["cases"]:
            directory = root / case["candidate"]
            self.assertTrue(directory.is_dir())
            self.assertFalse((directory / "controller").exists())
            self.assertFalse((directory / "expected.json").exists())
            for name, item in case["files"].items():
                path = directory / name
                if item.get("type") == "symlink":
                    self.assertTrue(path.is_symlink())
                else:
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
        for field in ("source_hashes", "grader_hashes", "controller_hashes"):
            for name, expected_hash in result[field].items():
                self.assertEqual(hashlib.sha256((PLUGIN / name).read_bytes()).hexdigest(), expected_hash)

    def test_actual_starter_graders_accept_references_and_reject_mutants(self):
        with patch.object(tasks.Task, "demonstration_answer", side_effect=AssertionError("Not an independent answer")):
            root, result = self.run_audit(execute=True)
        self.assertEqual(result["status"], "passed")
        for task_id in tasks.TASK_IDS:
            controls = [c for c in result["cases"] if c["task"] == task_id]
            self.assertEqual(sum(c["expected"] == "accept" for c in controls), 2)
            self.assertGreater(sum(c["kind"] == "semantic-mutant" for c in controls), 4)
            for case in controls:
                self.assertEqual(case["expected"], case["observed"], case["id"])
                self.assertEqual(case["status"], "passed")
                self.assertIsNone(case["model"])
                for item in case["evidence"]:
                    path = root / item["path"]
                    self.assertTrue(path.is_relative_to(root / "controller"))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
        self.assertFalse(result["summary"]["surviving_mutants"])
        self.assertFalse(result["summary"]["infrastructure_errors"])

    def test_distinct_topological_order_is_actually_graded(self):
        root, result = self.run_audit(execute=True, seed=17)
        directory = root / "candidates/deployment-order"
        first = json.loads((directory / "reference/answer.json").read_text())
        alternate = json.loads((directory / "alternate-valid/answer.json").read_text())
        self.assertNotEqual(first["deployment_order"], alternate["deployment_order"])
        for name in ("reference", "alternate-valid"):
            case = next(c for c in result["cases"] if c["id"] == f"deployment-order/{name}")
            self.assertEqual(case["observed"], "accept")

    def test_reference_solver_detects_wrong_grader_expectations(self):
        factory = tasks.make_task
        def changed_expected(task_id, seed):
            task = factory(task_id, seed)
            if task_id == "invoice-reconciliation":
                task.expected["total_outstanding_cents"] += 7
            return task
        with patch.object(tasks, "make_task", side_effect=changed_expected):
            _, result = self.run_audit(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("invoice-reconciliation/reference", result["summary"]["rejected_valid"])

    def test_always_pass_grader_is_detected(self):
        with patch.object(tasks.Task, "grade", return_value={"success": True, "reason": "Always pass"}):
            _, result = self.run_audit(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["summary"]["surviving_mutants"]),
                         sum(c["expected"] == "reject" for c in result["cases"]))
        self.assertEqual(result["summary"]["killed_mutants"], [])

    def test_always_fail_grader_is_detected(self):
        with patch.object(tasks.Task, "grade", return_value={"success": False, "reason": "Always fail"}):
            _, result = self.run_audit(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["summary"]["rejected_valid"]), 2 * len(tasks.TASK_IDS))

    def test_grader_exceptions_never_kill_mutants(self):
        with patch.object(tasks.Task, "grade", side_effect=OSError("Injected unavailable filesystem")):
            _, result = self.run_audit(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["summary"]["infrastructure_errors"]), len(result["cases"]))
        self.assertEqual(result["summary"]["killed_mutants"], [])
        self.assertTrue(all(c["observed"] == "infrastructure_error" for c in result["cases"]))

    def test_invalid_grader_protocol_fails_closed(self):
        for index, response in enumerate(({}, [], {"success": 1}, {"success": "false"},
                                          {"success": True, "reason": object()})):
            with self.subTest(response=response), patch.object(tasks.Task, "grade", return_value=response):
                _, result = self.run_audit(name=f"bad-protocol-{index}", execute=True)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["summary"]["killed_mutants"], [])
                self.assertEqual(len(result["summary"]["infrastructure_errors"]), len(result["cases"]))

    def test_symlink_control_reaches_actual_grader_path_guard(self):
        root, result = self.run_audit(execute=True)
        cases = [c for c in result["cases"] if c["name"] == "symlink-answer"]
        for case in cases:
            link = root / case["candidate"] / "answer.json"
            self.assertTrue(link.resolve().is_relative_to(root / "controller"))
            self.assertEqual(case["observed"], "reject")
            self.assertIn("ValueError", case["reason"])


class AuditPathTests(AuditTestCase):
    def test_invalid_options_are_rejected_before_output_creation(self):
        options = [
            {"suite": "unknown"}, {"suite": None}, {"suite": ["starter"]},
            {"seed": True}, {"seed": 1.5}, {"seed": "42"}, {"execute": "true"},
            {"directory": ""}, {"directory": "bad\npath"}, {"directory": self.root / "../escape"},
            {"tools_dir": ""}, {"tools_dir": None}, {"tools_dir": self.root / "bad\0tools"},
        ]
        for index, overrides in enumerate(options):
            target = self.root / f"invalid-{index}"
            arguments = {"suite": "starter", "directory": target, "tools_dir": self.tools, **overrides}
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                audit.write_audit(**arguments)
            self.assertFalse(target.exists())

    def test_output_never_overwrites_artifacts_or_follows_output_symlink(self):
        root, result = self.run_audit()
        original = (root / "AUDIT.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            self.run_audit(execute=True)
        self.assertEqual((root / "AUDIT.json").read_bytes(), original)
        link = self.root / "linked-output"
        link.symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            audit.write_audit("starter", link, tools_dir=self.tools)
        (self.root / "unrelated").mkdir()
        (self.root / "unrelated/keep.txt").write_text("keep")
        with self.assertRaises(ValueError):
            self.run_audit(name="unrelated")
        self.assertEqual((self.root / "unrelated/keep.txt").read_text(), "keep")

    def test_output_cannot_overlap_installed_plugin_or_runtime(self):
        for destination, tools_dir in [
            (PLUGIN / "audit-not-allowed", self.tools),
            (self.tools / "audit", self.tools),
            (self.root / "outer", self.root / "outer/tools"),
        ]:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                audit.write_audit("starter", destination, tools_dir=tools_dir)
            self.assertFalse(destination.exists())
        bad_tools = self.root / "tools-file"
        bad_tools.write_text("not a directory")
        with self.assertRaisesRegex(ValueError, "tools_dir"):
            audit.write_audit("starter", self.root / "other", tools_dir=bad_tools)

    def test_process_timeout_and_output_limit_are_infrastructure_failures(self):
        helper = audit.load_cdk_helper()
        slow = audit.run_bounded([sys.executable, "-B", "-c", "import time; time.sleep(5)"],
                                 self.root, self.root / "timeout", env={}, timeout=0.1)
        self.assertTrue(slow["timed_out"])
        self.assertEqual(helper.verdict(slow, "verify")[0], "infrastructure_error")
        loud = audit.run_bounded([sys.executable, "-B", "-c", "print('x' * 8192)"],
                                 self.root, self.root / "loud", env={}, timeout=5)
        with patch.object(audit, "MAX_LOG_BYTES", 128):
            bounded = audit.run_bounded([sys.executable, "-B", "-c", "print('x' * 8192)"],
                                        self.root, self.root / "limited", env={}, timeout=5)
        self.assertEqual(loud["exit_code"], 0)
        self.assertTrue(bounded["output_limited"])
        self.assertEqual(helper.verdict(bounded, "verify")[0], "infrastructure_error")

    def test_installed_portability_with_no_repository_neighbor(self):
        installed = self.root / "installed plugin"
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        work = self.root / "work"
        work.mkdir()
        code = """
import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from bedrock_bench.audit import PLUGIN,write_audit
assert PLUGIN == Path(sys.argv[1]).parent
for suite,execute in [('starter',True),('aws-cdk-smoke',False)]:
 path=write_audit(suite,Path(sys.argv[2])/suite,execute=execute,tools_dir=Path(sys.argv[2])/'tools')
 data=json.loads(path.with_name('AUDIT.json').read_text())
 assert data['status']==('passed' if execute else 'planned'),data
 assert all((PLUGIN/p).is_file() for p in data['source_hashes'])
print('installed portability passed')
"""
        process = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(installed / "scripts"), str(work)],
                                 cwd=work, capture_output=True, text=True, timeout=25)
        self.assertEqual(process.returncode, 0, process.stderr)
        helper = subprocess.run([sys.executable, "-I", "-B",
            str(installed / "skills/audit-benchmark/scripts/cdk_controls.py"), "--out", str(work / "helper-plan"),
            "--tools-dir", str(work / "tools")], cwd=work, capture_output=True, text=True, timeout=25)
        self.assertEqual(helper.returncode, 0, helper.stderr)
        self.assertTrue((work / "helper-plan/AUDIT.json").exists())


class CDKAuditTests(AuditTestCase):
    def test_concrete_plan_preserves_actual_verifier_and_submission_boundary(self):
        with patch("subprocess.Popen", side_effect=AssertionError("A plan must not execute")):
            root, result = self.run_audit(suite="aws-cdk-smoke")
        helper = audit.load_cdk_helper()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["plan_validation"]["status"], "passed")
        self.assertFalse(result["plan_validation"]["actual_grader_executed"])
        self.assertEqual((root / "controller/verifier/verify.cjs").read_bytes(),
                         (helper.SOURCE / "tests/verify.cjs").read_bytes())
        self.assertGreater(len(result["cases"]), 25)
        reference = next(c for c in result["cases"] if c["kind"] == "reference")
        original = root / reference["candidate"]
        for case in result["cases"]:
            directory = root / case["candidate"]
            changed = {name for name in helper.PROJECT_FILES
                       if (original / name).read_bytes() != (directory / name).read_bytes()}
            if case["kind"] != "reference":
                self.assertTrue(changed, case["id"])
                self.assertLessEqual(changed, {"lib/stack.ts", "lambda/handler.js"})
            self.assertFalse((directory / "verify.cjs").exists())
        scoped = next(c for c in result["cases"] if c["name"] == "iam-unrelated-service")
        self.assertIn("iam:PassRole", (root / scoped["candidate"] / "lib/stack.ts").read_text())
        self.assertEqual(scoped["expected"], "reject")

    def test_mutation_drift_and_unsubmitted_file_edits_are_rejected(self):
        helper = audit.load_cdk_helper()
        reference = {"lib/stack.ts": b"original"}
        for patches in [
            [{"file": "package.json", "before": "x", "after": "y"}],
            [{"file": "lib/stack.ts", "before": "missing", "after": "y"}],
            [{"file": "lib/stack.ts", "before": "original", "after": "original"}],
        ]:
            with self.assertRaises(ValueError):
                helper.apply_patches(reference, patches)
        with self.assertRaisesRegex(ValueError, "one exact match"):
            helper.apply_patches({"lib/stack.ts": b"x x"}, [{"file": "lib/stack.ts", "before": "x", "after": "y"}])

    @unittest.skipUnless(shutil.which("node"), "Optional local Node runtime is unavailable")
    def test_missing_cdk_runtime_is_unavailable_despite_valid_handler_controls(self):
        root, result = self.run_audit(suite="aws-cdk-smoke", execute=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["fixture_validation"]["status"], "passed")
        self.assertFalse(result["fixture_validation"]["actual_packaged_grader"])
        self.assertEqual(result["summary"]["killed_mutants"], [])
        self.assertEqual(len(result["summary"]["unavailable"]), len(result["cases"]))
        self.assertFalse(any(root.glob("controller/cases/*/*/verify")))
        self.assertIn("No local node_modules", result["preparation_needed"][0])

    @unittest.skipUnless(shutil.which("node"), "Optional local Node runtime is unavailable")
    def test_incomplete_runtime_preflight_does_not_reject_mutants(self):
        (self.tools / "node_modules").mkdir(parents=True)
        root, result = self.run_audit(suite="aws-cdk-smoke", execute=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["summary"]["killed_mutants"], [])
        self.assertIn("not ready", result["preparation_needed"][0])
        self.assertTrue((root / "controller/runtime/probe/stderr.txt").is_file())

    def test_explicit_unavailable_runtime_remains_distinct_from_grader_rejection(self):
        helper = audit.load_cdk_helper()
        with patch.object(audit, "load_cdk_helper", return_value=helper), \
                patch.object(helper, "choose_runtime", return_value=(None, "Offline test: runtime absent")), \
                patch.object(helper, "fixture_check"):
            _, result = self.run_audit(suite="aws-cdk-smoke", execute=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["summary"]["killed_mutants"], [])
        self.assertEqual(result["summary"]["infrastructure_errors"], [])

    def test_arbitrary_image_selector_commands_are_not_executed(self):
        helper = audit.load_cdk_helper()
        self.tools.mkdir()
        (self.tools / "image-id.txt").write_text("node -e 'throw new Error(\"untrusted\")'")
        with patch("subprocess.Popen", side_effect=AssertionError("Must not execute selector content")):
            runtime, reason = helper.choose_runtime(self.root, self.tools)
        self.assertIsNone(runtime)
        self.assertIn("commands", reason)

    def test_no_stderr_guessing_or_malformed_transport_success(self):
        helper = audit.load_cdk_helper()
        base = {"stdout": "", "stderr": "AssertionError", "exit_code": 1,
                "timed_out": False, "output_limited": False, "error": None}
        for changes in [
            {}, {"timed_out": True}, {"error": "Missing executable"},
            {"exit_code": 137}, {"stdout": helper.MARKER + "{}"},
            {"stdout": helper.MARKER + json.dumps({"phase": "verify", "observed": "reject", "verifier_invoked": False})},
            {"exit_code": 0, "stdout": helper.MARKER + json.dumps({"phase": "verify", "observed": "reject", "verifier_invoked": True})},
            {"stdout": helper.MARKER + json.dumps({"phase": "verify", "observed": "reject", "verifier_invoked": True,
                                                 "errors": "AssertionError", "offline_blocks": []})},
            {"stdout": helper.MARKER + json.dumps({"phase": "verify", "observed": "reject", "verifier_invoked": True,
                                                 "errors": [], "offline_blocks": []})},
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(helper.verdict({**base, **changes}, "verify")[0], "infrastructure_error")

    def test_container_command_is_offline_and_timeout_removes_only_created_container(self):
        helper = audit.load_cdk_helper()
        runtime = {"kind": "local-docker", "docker": "/usr/bin/docker", "host": "unix:///local/docker.sock",
                   "image": "sha256:" + "a" * 64, "modules": "/app/node_modules"}
        project = self.root / "candidate"
        project.mkdir()
        directory = self.root / "controller/phase"
        container_id = "b" * 64
        calls = []
        def fake_process(argv, cwd, output, **options):
            calls.append(argv)
            if "run" in argv:
                directory.mkdir(parents=True)
                (directory / "container.id").write_text(container_id)
                return {"exit_code": -9, "timed_out": True, "output_limited": False, "error": None}
            return {"exit_code": 0, "timed_out": False, "output_limited": False, "error": None}
        with patch.object(audit, "run_bounded", side_effect=fake_process):
            result = helper.run_phase(runtime, self.root, project, "verify", directory)
        launch, cleanup = calls
        self.assertIn("--pull=never", launch)
        self.assertIn("--network=none", launch)
        self.assertIn("--read-only", launch)
        self.assertEqual(launch[launch.index("--entrypoint") + 1], "node")
        self.assertIn(f"{self.root / 'controller'}:/audit:ro", launch)
        self.assertEqual(cleanup[-3:], ["rm", "--force", container_id])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["container_cleanup"]["exit_code"], 0)

    @unittest.skipUnless(shutil.which("node"), "Optional local Node runtime is unavailable")
    def test_driver_classifies_actual_error_objects_and_blocks_external_operations(self):
        helper = audit.load_cdk_helper()
        project = self.root / "candidate"
        project.mkdir()
        modules = self.root / "node_modules"
        modules.mkdir()
        verifier = self.root / "verifier.cjs"
        node = shutil.which("node")
        probes = [
            ('require("node:assert/strict").fail("semantic failure");', "reject"),
            ('throw new TypeError("unexpected verifier bug");', "infrastructure_error"),
            ('require("missing-audit-dependency");', "infrastructure_error"),
            ('console.error("AssertionError");process.exitCode=1;', "infrastructure_error"),
            ('try { require("node:http").get("http://127.0.0.1:9"); } catch {}', "infrastructure_error"),
            ('try { require("node:child_process").execSync("exit 0"); } catch {}', "infrastructure_error"),
            ('console.log("successful verifier");', "accept"),
        ]
        for index, (source, expected) in enumerate(probes):
            verifier.write_text(source)
            process = audit.run_bounded([node, "--require", str(audit.SKILL / "scripts/offline.cjs"),
                str(audit.SKILL / "scripts/cdk_driver.cjs"), "verify", str(project), str(modules),
                str(verifier), str(project / "package.json")], self.root, self.root / f"driver-{index}",
                env=helper.environment(node), timeout=8)
            observed, reason, row = helper.verdict(process, "verify")
            self.assertEqual(observed, expected, (source, reason, process))

    @unittest.skipUnless(os.environ.get("BEDROCK_BENCH_AUDIT_TEST_TOOLS"), "Set BEDROCK_BENCH_AUDIT_TEST_TOOLS for pinned CDK integration")
    def test_alternate_valid_iam_against_actual_verifier(self):
        # A focused positive check can run independently of the full mutation
        # audit while a parent task owns the full verifier run.
        root, data = self.run_audit(suite="aws-cdk-smoke")
        case = next(c for c in data["cases"] if c["kind"] == "alternate-valid")
        data["cases"] = [case]
        helper = audit.load_cdk_helper()
        helper.execute(root, data, Path(os.environ["BEDROCK_BENCH_AUDIT_TEST_TOOLS"]).resolve())
        self.assertEqual(case["observed"], "accept", case.get("reason"))
        self.assertEqual(data["fixture_validation"]["status"], "passed")
        document = json.loads((root / case["candidate"] / "cdk.out/Benchmark.template.json").read_text())
        resources = document["Resources"]
        function = next(r for r in resources.values() if r["Type"] == "AWS::Lambda::Function")
        role = resources[function["Properties"]["Role"]["Fn::GetAtt"][0]]["Properties"]
        self.assertFalse(role.get("ManagedPolicyArns"))
        inline_actions = [action for policy in role["Policies"]
                          for statement in policy["PolicyDocument"]["Statement"]
                          for action in statement["Action"]]
        self.assertEqual({a.lower() for a in inline_actions},
                         {"logs:createloggroup", "logs:createlogstream", "logs:putlogevents"})
        policies = [r["Properties"]["PolicyDocument"] for r in resources.values() if r["Type"] == "AWS::IAM::Policy"]
        recorded = [action for policy in policies for statement in policy["Statement"]
                    for action in (statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]])]
        self.assertIn("DynamoDB:pUtItEm", recorded)
        self.assertIn("SQS:receiveMessage", recorded)

    @unittest.skipUnless(os.environ.get("BEDROCK_BENCH_AUDIT_TEST_TOOLS"), "Set BEDROCK_BENCH_AUDIT_TEST_TOOLS for pinned CDK integration")
    def test_actual_packaged_cdk_verifier_controls(self):
        path = audit.write_audit("aws-cdk-smoke", self.root / "integration", execute=True,
                                tools_dir=os.environ["BEDROCK_BENCH_AUDIT_TEST_TOOLS"])
        data = json.loads(path.with_name("AUDIT.json").read_text())
        self.assertEqual(data["fixture_validation"]["status"], "passed")
        self.assertEqual(data["summary"]["infrastructure_errors"], [])
        self.assertEqual(data["summary"]["unavailable"], [])
        self.assertEqual(data["summary"]["rejected_valid"], [])
        self.assertTrue(all(c["observed"] in {"accept", "reject"} for c in data["cases"]))
        # Assert effectiveness without requiring a production grader to retain
        # today's gaps. If a survivor is fixed later, that is an improvement.
        self.assertIn("cdk-sqs-lambda-dynamodb/iam-wildcard-resource", data["summary"]["killed_mutants"])
        self.assertIn("cdk-sqs-lambda-dynamodb/handler-retry-write", data["summary"]["killed_mutants"])


if __name__ == "__main__":
    unittest.main()
