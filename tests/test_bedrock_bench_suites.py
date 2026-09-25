"""Offline suite integration tests. Fake CLIs exercise process/report boundaries."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bedrock-bench"
sys.path.insert(0, str(PLUGIN / "scripts"))

from bedrock_bench.cli import main
from bedrock_bench.report import compare
from bedrock_bench.suites import (
    AWS_REGISTRY, SuiteExperiment, SuiteLimits, SuiteTarget,
    catalog, load_experiment, task_catalog,
)
from bedrock_bench.tooling import prepare
from bedrock_bench.upstream import (
    agent_config, aws_environment_action, execute_suite,
    harness_metadata, import_trial, job_config, process_environment, usage_and_cost,
)


def experiment(**changes):
    return SuiteExperiment(**{
        "name": "Offline adapter test", "suite": "terminal-bench",
        "targets": [SuiteTarget("candidate", "codex", "openai", "test-model")],
        "tasks": ["fix-git"], **changes,
    })


def rate():
    return {
        "provider": "openai", "model": "test-model", "region": None, "service_tier": "default",
        "as_of": "2026-09-24", "source": "https://example.com/fictional-test-rates",
        "input_usd_per_million": 2, "cached_input_usd_per_million": 0.5,
        "output_usd_per_million": 10,
    }


def raw_trial(**changes):
    return {
        "task_name": "fix-git", "trial_name": "fix-git__test", "task_checksum": "fixture-checksum",
        "task_id": {"git_commit_id": task_catalog("terminal-bench")["fix-git"]["git_commit_id"]},
        "agent_info": {"name": "codex", "version": "fixture-version"},
        "agent_result": {"n_input_tokens": 1000, "n_cache_tokens": 400,
                         "n_output_tokens": 100, "cost_usd": None},
        "verifier_result": {"rewards": {"reward": 1.0}}, "exception_info": None,
        "agent_execution": {"started_at": "2026-09-24T00:00:00Z", "finished_at": "2026-09-24T00:00:02Z"},
        **changes,
    }


PROCESS = {"exit_code": 0, "timed_out": False, "wall_seconds": 4.0}


class SuitePlanningTests(unittest.TestCase):
    def test_catalog_is_packaged_and_sources_are_commit_pinned(self):
        suites = {entry["id"]: entry for entry in catalog()}
        self.assertEqual(set(suites), {"starter", "aws-cdk-smoke", "terminal-bench", "swe-bench", "aws-bench"})
        for suite in ("terminal-bench", "swe-bench", "aws-bench"):
            for task in task_catalog(suite).values():
                self.assertRegex(task["git_commit_id"], r"^[0-9a-f]{40}$")
                self.assertTrue(task["git_url"].startswith("https://github.com/"))
        self.assertTrue(suites["aws-bench"]["requires_aws_environment"])
        self.assertFalse(suites["aws-cdk-smoke"]["requires_aws_environment"])

    def test_init_selects_one_upstream_task_and_dry_run_does_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "experiment.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", "--suite", "terminal-bench", "--runner", "codex",
                                       "--provider", "openai", "--model", "exact-model", "--out", str(path)]), 0)
            config = load_experiment(path)
            self.assertEqual(config.tasks, ["fix-git"])
            self.assertEqual(config.targets[0].model, "exact-model")
            with patch("bedrock_bench.upstream.execute_suite", side_effect=AssertionError("must not run")), \
                 patch("subprocess.Popen", side_effect=AssertionError("must not start processes")):
                outputs = []
                for command in ("plan", "run"):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(main([command, str(path)]), 0)
                    outputs.append(json.loads(output.getvalue()))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0]["attempts"], 1)

    def test_limits_and_task_selection_reject_invalid_values(self):
        for changes in ({"tasks": []}, {"tasks": ["*"]}, {"tasks": ["fix-git", "fix-git"]},
                        {"repetitions": 0}, {"seed": True}):
            with self.assertRaises(ValueError):
                experiment(**changes)
        with self.assertRaises(ValueError):
            SuiteLimits(timeout_seconds=0)
        with self.assertRaises(ValueError):
            SuiteLimits(timeout_seconds=1500, process_timeout_seconds=100)
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["init", "--suite", "aws-cdk-smoke", "--runner", "codex",
                                   "--provider", "openai", "--model", "test-model",
                                   "--timeout-seconds", "0", "--out", str(Path(temp) / "x.json")]), 2)

    def test_relative_paths_are_resolved_against_experiment_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: fixture\ndescription: Test\n---\n")
            config = experiment().to_dict()
            config["tools_dir"] = "tools"
            config["targets"][0]["skills"] = ["skill"]
            path = root / "experiment.json"
            path.write_text(json.dumps(config))
            loaded = load_experiment(path)
            self.assertEqual(loaded.tools_dir, str((root / "tools").resolve()))
            self.assertEqual(loaded.targets[0].skills, [str(skill.resolve())])

    def test_task_protocol_changes_with_limits_or_task_source(self):
        before = experiment().protocol()
        after = experiment(limits=SuiteLimits(timeout_seconds=400)).protocol()
        self.assertNotEqual(before, after)
        self.assertEqual(before, experiment(targets=[SuiteTarget("other", "opencode", "openrouter", "another-model")]).protocol())

    def test_preparation_and_environment_operations_require_execution(self):
        with patch("subprocess.run", side_effect=AssertionError("must not install")):
            result = prepare("aws-bench", "/tmp/fixture-tools")
        self.assertIn("@ea65432b5ce1d838b932728fcec4e02f493761a5", result["requirement"])
        config = experiment(suite="aws-bench", tasks=["describe-cloudformation-stack-resources"],
                            aws_environment="fixture-environment")
        with patch("bedrock_bench.upstream.run_process", side_effect=AssertionError("must not call AWS")):
            plan = aws_environment_action(config, "setup", "/tmp/unused")
        self.assertTrue(plan["creates_or_changes_aws_resources"])
        self.assertIn("aws-bench-quickstart@0.7.2", plan["command"])
        self.assertIn(str(AWS_REGISTRY), plan["command"])


class UpstreamConfigTests(unittest.TestCase):
    def test_harbor_jobs_use_one_pinned_task_without_implicit_retries(self):
        for suite, task in (("terminal-bench", "fix-git"), ("swe-bench", "django__django-15098")):
            exp = experiment(suite=suite, tasks=[task])
            config = job_config(exp, exp.targets[0], task, Path("/tmp/trial"))
            self.assertEqual(len(config["tasks"]), 1)
            self.assertEqual(config["tasks"][0]["git_commit_id"], task_catalog(suite)[task]["git_commit_id"])
            self.assertEqual(config["retry"]["max_retries"], 0)
            self.assertEqual(config["n_attempts"], 1)
            self.assertEqual(config["n_concurrent_trials"], 1)
            if suite == "swe-bench":
                self.assertEqual(process_environment(exp, exp.targets[0])["DOCKER_DEFAULT_PLATFORM"], "linux/amd64")
                self.assertEqual(exp.plan()["docker_platform"], "linux/amd64")

    def test_aws_job_uses_scenario_dataset_and_exact_task_filter(self):
        exp = experiment(suite="aws-bench", tasks=["describe-cloudformation-stack-resources"],
                         aws_environment="fixture-environment")
        config = job_config(exp, exp.targets[0], exp.tasks[0], Path("/tmp/trial"))
        self.assertNotIn("tasks", config)
        self.assertEqual(config["dataset"]["task_names"], exp.tasks)
        self.assertEqual(config["dataset"]["registry_path"], str(AWS_REGISTRY))
        self.assertEqual(config["env_name"], "fixture-environment")
        self.assertEqual(config["retry"]["max_retries"], 0)

    def test_bedrock_auth_is_not_serialized_and_openai_does_not_autoselect_bedrock(self):
        target = SuiteTarget("candidate", "codex", "amazon-bedrock", "exact-bedrock-model", region="us-west-2")
        exp = experiment(targets=[target])
        with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "fixture-token-value"}):
            config = agent_config(exp, target)
            environment = process_environment(exp, target)
            self.assertNotIn("fixture-token-value", json.dumps(config))
            self.assertEqual(config["import_path"], "bedrock_bench.harbor_agents:BedrockCodex")
            self.assertEqual(config["model_name"], "exact-bedrock-model")
            self.assertEqual(environment["BEDROCK_BENCH_MODEL_REGION"], "us-west-2")
            openai = experiment()
            self.assertNotIn("AWS_BEARER_TOKEN_BEDROCK", process_environment(openai, openai.targets[0]))

    def test_skill_sources_and_agent_versions_reach_harbor(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "SKILL.md").write_text("---\nname: fixture\ndescription: Test\n---\n")
            target = SuiteTarget("with-skill", "codex", "openai", "test-model",
                                 skills=[temp], agent_version="0.118.0", reasoning_effort="high")
            config = agent_config(experiment(targets=[target]), target)
            self.assertEqual(config["skills"], [str(Path(temp).resolve())])
            self.assertEqual(config["kwargs"], {"version": "0.118.0", "reasoning_effort": "high"})


class UpstreamAccountingTests(unittest.TestCase):
    def test_usage_estimate_includes_cache_once(self):
        target = experiment().targets[0]
        result = usage_and_cost(raw_trial(), target, rate(), complete=True)
        self.assertAlmostEqual(result["cost_usd"], 0.0024)
        self.assertEqual(result["cost_basis"], ["rate_card_estimate"])
        partial = usage_and_cost(raw_trial(), target, rate(), complete=False)
        self.assertIsNone(partial["cost_usd"])
        self.assertAlmostEqual(partial["known_cost_subtotal_usd"], 0.0024)

    def test_upstream_prices_are_estimates_and_unknown_zero_is_not_free(self):
        target = experiment().targets[0]
        raw = raw_trial()
        raw["agent_result"]["cost_usd"] = 0
        self.assertIsNone(usage_and_cost(raw, target, None, complete=True)["cost_usd"])
        raw["agent_result"]["cost_usd"] = 0.4
        cost = usage_and_cost(raw, target, None, complete=True)
        self.assertEqual(cost["cost_usd"], 0.4)
        self.assertEqual(cost["cost_basis"], ["runner_estimate"])
        raw["agent_result"]["cost_usd"] = None
        raw["agent_result"]["model_usage"] = {"different-model": {"n_input_tokens": 1000}}
        self.assertIsNone(usage_and_cost(raw, target, rate(), complete=True)["cost_usd"])

    def test_partial_multistep_usage_does_not_hide_missing_spend(self):
        raw = raw_trial(agent_result=None, step_results=[
            {"agent_result": raw_trial()["agent_result"]}, {"agent_result": {}},
        ])
        result = usage_and_cost(raw, experiment().targets[0], rate(), complete=True)
        self.assertIsNone(result["cost_usd"])
        self.assertAlmostEqual(result["known_cost_subtotal_usd"], 0.0024)
        self.assertIsNone(result["usage"]["input_tokens"])

    def test_missing_mismatched_and_invalid_rewards_fail_closed(self):
        exp = experiment()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            empty = import_trial(exp, exp.targets[0], "fix-git", root, PROCESS)
            self.assertEqual(empty["status"], "runner_error")
            self.assertIsNone(empty["cost_usd"])
            directory = root / "upstream/job/trial"
            directory.mkdir(parents=True)
            for data in [
                raw_trial(task_name="wrong-task"),
                raw_trial(task_id=None),
                raw_trial(task_id={"git_commit_id": "different-commit"}),
                raw_trial(verifier_result={"rewards": {"reward": True}}),
                raw_trial(verifier_result={"rewards": [1]}),
                raw_trial(verifier_result={"rewards": {"accuracy": 1}}),
                raw_trial(agent_result={"model_usage": ["invalid-shape"]}),
                raw_trial(exception_info={"exception_type": "VerifierError"}),
            ]:
                (directory / "result.json").write_text(json.dumps(data))
                result = import_trial(exp, exp.targets[0], "fix-git", root, PROCESS)
                self.assertFalse(result["success"])
                self.assertIsNone(result["cost_usd"])
            (directory / "result.json").write_text(
                json.dumps(raw_trial()).replace('"reward": 1.0', '"reward": 1e400'))
            result = import_trial(exp, exp.targets[0], "fix-git", root, PROCESS)
            self.assertEqual(result["status"], "runner_error")


class UpstreamExecutionTests(unittest.TestCase):
    def test_stale_harness_is_rejected_before_a_trial_can_run(self):
        with tempfile.TemporaryDirectory() as temp:
            python = Path(temp) / "harbor/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            completed = subprocess.CompletedProcess([], 0, json.dumps({
                "packages": {"harbor": "0.9.0"}, "direct_urls": {},
            }), "")
            with patch("bedrock_bench.upstream.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(ValueError, "pinned version"):
                    harness_metadata(experiment(tools_dir=temp))

    def test_real_process_bridge_preserves_failed_spend_logs_and_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "tools/harbor/bin/harbor"
            binary.parent.mkdir(parents=True)
            binary.write_text(f"#!{sys.executable}\n" + r'''
import json,os,pathlib,sys
config=json.loads(pathlib.Path(sys.argv[sys.argv.index("--config")+1]).read_text())
agent=config["agents"][0]
task=pathlib.Path(config["tasks"][0]["path"]).name
directory=pathlib.Path(config["jobs_dir"])/config["job_name"]/"trial"
directory.mkdir(parents=True)
success=agent["model_name"]=="passing-model"
value={
 "task_name":task,"trial_name":task+"__fixture","task_checksum":"fixture-checksum",
 "task_id":config["tasks"][0],
 "agent_info":{"name":agent["name"],"version":"fixture-version"},
 "agent_result":{"n_input_tokens":100,"n_cache_tokens":0,"n_output_tokens":10,"cost_usd":1 if success else 3},
 "verifier_result":{"rewards":{"reward":1 if success else 0}},
 "exception_info":None,
}
(directory/"result.json").write_text(json.dumps(value))
(directory/"agent").mkdir()
(directory/"agent/trajectory.json").write_text(json.dumps({"fixture_secret":os.environ.get("OPENAI_API_KEY")}))
print("fixture completed")
''')
            binary.chmod(0o755)
            exp = experiment(tools_dir=str(root / "tools"), targets=[
                SuiteTarget("pass", "codex", "openai", "passing-model"),
                SuiteTarget("fail", "codex", "openai", "failing-model"),
            ])
            with self.assertRaises(ValueError):
                execute_suite(exp, root / "results")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-secret-not-for-logs"}), \
                    patch("bedrock_bench.upstream.harness_metadata",
                          return_value={"packages": {"harbor": "fixture"}, "verified": True}):
                output, run = execute_suite(exp, root / "results", allow_live=True)
            self.assertEqual(len(run["attempts"]), 2)
            self.assertEqual(sum(row["success"] for row in run["attempts"]), 1)
            self.assertEqual(sum(row["cost_usd"] for row in run["attempts"]), 4)
            self.assertTrue((output / "REPORT.md").is_file())
            summary = compare([run])
            self.assertEqual(summary["upstream_trials"][0]["task_id"], "fix-git")
            self.assertIn("git_commit_id", summary["upstream_trials"][0]["source"])
            for trace in output.rglob("trajectory.json"):
                self.assertNotIn("fixture-secret-not-for-logs", trace.read_text())
                self.assertIn("[REDACTED]", trace.read_text())
            for row in run["attempts"]:
                self.assertTrue((output / row["trace"]).is_file())
                self.assertTrue(row["upstream"]["result"].endswith("result.json"))
            altered = copy.deepcopy(run)
            altered["run_id"] = "validation-copy"
            altered["validation_only"] = True
            with self.assertRaisesRegex(ValueError, "Reference"):
                compare([run, altered])


class InstalledUpstreamSchemaTests(unittest.TestCase):
    def test_bedrock_credentials_reach_the_harbor_execution_scope(self):
        python = ROOT / ".bench-tools/harbor/bin/python"
        if not python.is_file():
            self.skipTest("Optional Harbor environment has not been prepared")
        code = """
import json,os,tempfile
from pathlib import Path
from harbor.agents.factory import AgentFactory
from harbor.models.trial.config import AgentConfig
from bedrock_bench.suites import SuiteTarget
from bedrock_bench.upstream import agent_config
from bedrock_bench.suites import SuiteExperiment
os.environ['AWS_BEARER_TOKEN_BEDROCK']='fixture-inference-token'
os.environ['BEDROCK_BENCH_MODEL_REGION']='us-west-2'
for runner in ('codex','opencode'):
 target=SuiteTarget('candidate',runner,'amazon-bedrock','fixture-model',region='us-west-2')
 experiment=SuiteExperiment(name='Offline',suite='aws-cdk-smoke',targets=[target],tasks=['cdk-sqs-lambda-dynamodb'])
 config=AgentConfig.model_validate(agent_config(experiment,target))
 assert 'fixture-inference-token' not in config.model_dump_json()
 with tempfile.TemporaryDirectory() as temp:
  agent=AgentFactory.create_agent_from_config(config,logs_dir=Path(temp))
  # This is the public property Trial snapshots before setup and run.
  assert agent.extra_env['AWS_BEARER_TOKEN_BEDROCK']=='fixture-inference-token'
  assert agent.extra_env['AWS_REGION']=='us-west-2'
  if runner=='codex':
   assert agent._build_effective_config(None)['model_provider']=='amazon-bedrock'
  assert agent.to_agent_info().model_info.provider=='amazon-bedrock'
print('validated')
"""
        env = {**os.environ, "PYTHONPATH": str(PLUGIN / "scripts")}
        result = subprocess.run([str(python), "-c", code], env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_generated_configs_round_trip_through_installed_schemas(self):
        """Optional integration: prepare both runtimes to enable this test."""
        tools = ROOT / ".bench-tools"
        if not all((tools / harness / "bin/python").exists() for harness in ("harbor", "aws-bench")):
            self.skipTest("Optional harness environments have not been prepared")
        code = """
import json,sys
if sys.argv[1]=='aws-bench':
 from aws_bench.cli.job_config import AwsBenchJobConfig as Model
else:
 from harbor.models.job.config import JobConfig as Model
config=json.load(sys.stdin)
model=Model.model_validate(config)
out=model.model_dump(mode='json')
assert out['n_attempts']==1 and out['n_concurrent_trials']==1
assert out['retry']['max_retries']==0
assert out['agents'][0]['model_name']=='test-model'
assert out['agents'][0]['override_timeout_sec']==300
if sys.argv[1]=='aws-bench':
 assert out['dataset']['task_names']==['describe-cloudformation-stack-resources']
 assert out['dataset']['registry_path'].endswith('aws-registry.json')
else:
 assert len(out['tasks'])==1
print('validated')
"""
        for suite, task in (("aws-cdk-smoke", "cdk-sqs-lambda-dynamodb"),
                            ("terminal-bench", "fix-git"), ("swe-bench", "django__django-15098"),
                            ("aws-bench", "describe-cloudformation-stack-resources")):
            exp = experiment(suite=suite, tasks=[task],
                             tools_dir=str(tools),
                             aws_environment="fixture" if suite == "aws-bench" else None)
            self.assertTrue(harness_metadata(exp)["verified"])
            config = job_config(exp, exp.targets[0], task, "/tmp/fixture")
            completed = subprocess.run([str(tools / exp.harness / "bin/python"), "-c", code, exp.harness],
                                       input=json.dumps(config), capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
