"""Offline design behavior: runnable configs, matched trials, provenance, and budgets."""

import contextlib
import copy
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bedrock-bench"
ASSETS = PLUGIN / "skills/design-experiment/assets"
sys.path.insert(0, str(PLUGIN / "scripts"))

from bedrock_bench.design import MAX_ATTEMPTS, write_design
from bedrock_bench.suites import directory_digest, load_experiment, task_catalog


def offline_brief():
    return json.loads((ASSETS / "offline-demo.brief.json").read_text())


def rate(model="chosen/model:exact", **changes):
    return {
        "provider": "openai", "model": model, "region": None, "service_tier": "default",
        "as_of": "2026-09-24", "source": "https://example.invalid/fictional-test-rates",
        "input_usd_per_million": 2, "cached_input_usd_per_million": 0.5,
        "cache_write_input_usd_per_million": 3, "output_usd_per_million": 10,
        **changes,
    }


def budget_brief():
    brief = offline_brief()
    target = brief["base_experiment"]["targets"][0]
    target.update(runner="native", provider="openai", model="chosen/model:exact")
    brief["conditions"][1]["target_changes"]["fixture"]["model"] = "chosen/model:other"
    brief["base_experiment"]["rate_cards"] = [rate(), rate("chosen/model:other")]
    brief["budget"] = {
        "max_usd": 0.1, "reserve_fraction": 0.25,
        "usage_per_attempt": [
            {
                "condition": "baseline", "target": "fixture", "source": "Fictional unit-test assumption",
                "input_tokens": 1000, "cached_input_tokens": 400, "cache_write_input_tokens": 100,
                "output_tokens": 200, "reasoning_output_tokens": 50,
            },
            {
                "condition": "imperfect", "target": "fixture", "source": "Fictional unit-test assumption",
                "input_tokens": 2000, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                "output_tokens": 500, "reasoning_output_tokens": 100,
            },
        ],
    }
    return brief


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def generate(self, brief=None, *, source_name="brief.json", output_name="design"):
        source = self.root / source_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(offline_brief() if brief is None else brief, indent=2))
        path = write_design(source, self.root / output_name)
        return path, json.loads(path.with_suffix(".json").read_text())

    def skill(self, name):
        path = self.root / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\nname: fixture\ndescription: Offline fixture skill\n---\n")
        return path

    def cdk_brief(self):
        brief = json.loads((ASSETS / "aws-cdk-skills-ablation.brief.json").read_text())
        brief["base_experiment"]["targets"][0].update(
            model="user-selected-model-id-v1:0", region="us-west-2", agent_version="0.114.0"
        )
        return brief

    def test_offline_example_emits_normal_validated_configs_without_execution(self):
        with patch("bedrock_bench.engine.execute", side_effect=AssertionError("must not execute")), \
             patch("bedrock_bench.upstream.execute_suite", side_effect=AssertionError("must not execute")), \
             patch("subprocess.Popen", side_effect=AssertionError("must not launch processes")):
            path, design = self.generate()
        self.assertIsInstance(path, Path)
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.name, "DESIGN.md")
        self.assertEqual(
            {p.relative_to(path.parent).as_posix() for p in path.parent.rglob("*") if p.is_file()},
            {"DESIGN.md", "DESIGN.json", "brief.json", "experiment.json",
             "conditions/baseline.json", "conditions/imperfect.json"},
        )
        self.assertEqual(design["counts"], {
            "distinct_tasks": 3, "repetitions": 2, "base_targets": 1, "conditions": 2,
            "target_condition_cells": 2, "attempts_per_cell": 6,
            "attempts_per_condition": 6, "total_attempts": 12, "catalog_tasks": 3,
        })
        self.assertFalse(design["execution"]["executed"])
        self.assertTrue(design["execution"]["condition_configs_are_alternatives"])
        self.assertEqual(design["varied_fields"], ["model"])
        primary = load_experiment(path.parent / "experiment.json")
        self.assertEqual(primary.plan(), design["experiment"]["plan"])
        self.assertEqual([t.model for t in primary.targets], ["fixture-efficient", "fixture-imperfect"])
        self.assertTrue(all(t.runner == "demo" and t.provider == "synthetic" for t in primary.targets))
        for condition in design["conditions"]:
            loaded = load_experiment(path.parent / condition["experiment"])
            self.assertEqual(loaded.plan(), condition["plan"])
            self.assertEqual(loaded.plan()["attempts"], 6)
            self.assertEqual(loaded.plan()["protocol_hash"], primary.plan()["protocol_hash"])
            self.assertEqual(loaded.seed, 42)
            self.assertEqual(loaded.tasks, primary.tasks)
            self.assertEqual(loaded.limits, primary.limits)

    def test_schedule_matches_runner_order_and_preserves_condition_mapping(self):
        path, design = self.generate()
        experiment = load_experiment(path.parent / "experiment.json")
        expected = [(t.id, task, rep) for rep in range(2) for task in experiment.tasks for t in experiment.targets]
        random.Random(42).shuffle(expected)
        actual = design["execution"]["schedule"]
        self.assertEqual([(r["target"], r["task"], r["repetition"] - 1) for r in actual], expected)
        lookup = {target: (c["id"], base) for c in design["conditions"] for base, target in c["target_ids"].items()}
        for index, row in enumerate(actual, 1):
            self.assertEqual((row["condition"], row["base_target"]), lookup[row["target"]])
            self.assertEqual(row["index"], index)
            self.assertEqual(row["fixture_seed"], 42 + row["repetition"] - 1)
            self.assertEqual(row["attempt_id"], f"{index:04}-{row['target']}-{row['task']}-r{row['repetition']}")
        self.assertEqual(len({r["attempt_id"] for r in actual}), 12)
        self.assertEqual(design["execution"]["summed_controller_timeout_seconds"], 12 * 120)
        self.assertFalse(design["execution"]["provider_sampling_seed_set"])

    def test_same_brief_is_reproducible_and_input_snapshot_and_hashes_match(self):
        path, design = self.generate()
        source = self.root / "brief.json"
        second = write_design(source, self.root / "second")
        self.assertEqual(path.with_suffix(".json").read_bytes(), second.with_suffix(".json").read_bytes())
        self.assertEqual((path.parent / "brief.json").read_bytes(), source.read_bytes())
        self.assertEqual(design["provenance"]["brief_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        for relative, digest in design["provenance"]["file_sha256"].items():
            self.assertEqual(hashlib.sha256((path.parent / relative).read_bytes()).hexdigest(), digest)
        different = offline_brief()
        different["base_experiment"]["seed"] = 0
        _, changed = self.generate(different, source_name="changed.json", output_name="changed")
        self.assertNotEqual(design["protocol_hash"], changed["protocol_hash"])
        self.assertNotEqual(design["execution"]["schedule"], changed["execution"]["schedule"])

    def test_long_base_ids_map_to_valid_runner_ids_without_changing_model_choices(self):
        brief = budget_brief()
        base_id = "candidate_" + "a" * 54
        brief["base_experiment"]["targets"][0]["id"] = base_id
        brief["conditions"][1]["target_changes"] = {
            base_id: brief["conditions"][1]["target_changes"]["fixture"]
        }
        for row in brief["budget"]["usage_per_attempt"]:
            row["target"] = base_id
        path, design = self.generate(brief)
        loaded = load_experiment(path.parent / "experiment.json")
        self.assertTrue(all(len(target.id) <= 64 for target in loaded.targets))
        self.assertEqual(len({target.id for target in loaded.targets}), 2)
        self.assertEqual([target.model for target in loaded.targets],
                         ["chosen/model:exact", "chosen/model:other"])
        self.assertTrue(all(list(c["target_ids"]) == [base_id] for c in design["conditions"]))
        self.assertEqual(design["budget"]["estimate_coverage"], 1)

    def test_relative_paths_use_input_brief_for_base_and_variant_not_cwd_or_output(self):
        base_skill = self.skill("inputs/skills/common")
        variant_skill = self.skill("inputs/skills/aws-cdk")
        brief = self.cdk_brief()
        brief["base_experiment"]["targets"][0]["skills"] = ["skills/common"]
        brief["base_experiment"]["tools_dir"] = "../prepared tools"
        brief["conditions"][1]["target_changes"]["candidate"]["skills"] = ["skills/common", "skills/aws-cdk"]
        elsewhere = self.root / "unrelated"
        elsewhere.mkdir()
        with contextlib.chdir(elsewhere):
            path, design = self.generate(brief, source_name="inputs/brief.json", output_name="outputs/plan")
            for relative in ["experiment.json", *[c["experiment"] for c in design["conditions"]]]:
                loaded = load_experiment(path.parent / relative)
                self.assertEqual(loaded.tools_dir, str(self.root / "prepared tools"))
                for target in loaded.targets:
                    self.assertEqual(target.model, "user-selected-model-id-v1:0")
                    self.assertEqual(target.region, "us-west-2")
                    self.assertEqual(target.agent_version, "0.114.0")
                    self.assertTrue(all(Path(s).is_absolute() for s in target.skills))
            loaded = load_experiment(path.parent / "experiment.json")
        self.assertEqual(loaded.targets[0].skills, [str(base_skill)])
        self.assertEqual(loaded.targets[1].skills, [str(base_skill), str(variant_skill)])
        self.assertEqual(design["provenance"]["skill_sha256"], {
            str(base_skill): directory_digest(base_skill), str(variant_skill): directory_digest(variant_skill),
        })
        self.assertEqual(design["varied_fields"], ["skills"])
        self.assertEqual(design["counts"]["distinct_tasks"], 1)
        self.assertEqual(design["counts"]["total_attempts"], 6)
        self.assertEqual(design["counts"]["catalog_tasks"], 1)
        self.assertTrue(all(r["fixture_seed"] is None for r in design["execution"]["schedule"]))
        self.assertEqual(design["execution"]["summed_controller_timeout_seconds"], 6 * 1500)
        self.assertEqual(design["provenance"]["task_sources"], loaded.plan()["sources"])
        codes = {item["code"] for item in design["limitations"]}
        self.assertTrue({"cdk_catalog_pilot", "single_task_pilot"} <= codes)
        self.assertNotIn("agent_version_unpinned", codes)

    def test_omitted_tools_dir_is_resolved_next_to_brief(self):
        self.skill("inputs/skills/aws-cdk")
        brief = self.cdk_brief()
        del brief["base_experiment"]["tools_dir"]
        path, _ = self.generate(brief, source_name="inputs/brief.json")
        loaded = load_experiment(path.parent / "experiment.json")
        self.assertEqual(loaded.tools_dir, str(self.root / "inputs/.bench-tools"))

    def test_multi_target_ablation_keeps_user_settings_in_each_stratum(self):
        self.skill("skills/aws-cdk")
        brief = self.cdk_brief()
        second = copy.deepcopy(brief["base_experiment"]["targets"][0])
        second.update(id="other", model="another-user-selected-model", region="eu-west-1", reasoning_effort="low")
        brief["base_experiment"]["targets"].append(second)
        brief["conditions"][1]["target_changes"]["other"] = {"skills": ["skills/aws-cdk"]}
        path, design = self.generate(brief)
        loaded = load_experiment(path.parent / "experiment.json")
        self.assertEqual(len(loaded.targets), 4)
        self.assertEqual(design["counts"]["total_attempts"], 12)
        for condition in design["conditions"]:
            targets = {target.id: target for target in loaded.targets}
            target = targets[condition["target_ids"]["other"]]
            self.assertEqual((target.model, target.region, target.reasoning_effort),
                             ("another-user-selected-model", "eu-west-1", "low"))
            self.assertEqual(condition["plan"]["attempts"], 6)

    def test_catalog_sources_are_packaged_and_pinned_without_preparation(self):
        self.skill("skills/aws-cdk")
        for suite, task in [("terminal-bench", "fix-git"), ("swe-bench", "django__django-15098")]:
            with self.subTest(suite=suite):
                brief = self.cdk_brief()
                brief["base_experiment"].update(suite=suite, tasks=[task])
                path, design = self.generate(brief, output_name=suite)
                source = design["provenance"]["task_sources"][0]
                self.assertEqual(source["git_commit_id"], task_catalog(suite)[task]["git_commit_id"])
                self.assertEqual(source["git_url"], task_catalog(suite)[task]["git_url"])
                self.assertFalse((self.root / ".bench-tools").exists())
                self.assertEqual(load_experiment(path.parent / "experiment.json").plan()["attempts"], 6)

    def test_unknown_costs_are_not_zero_and_overall_budget_is_not_enforced(self):
        _, design = self.generate()
        budget = design["budget"]
        self.assertIsNone(budget["estimated_inference_usd"])
        self.assertEqual(budget["known_estimate_subtotal_usd"], 0)
        self.assertEqual(budget["estimate_coverage"], 0)
        self.assertIsNone(budget["within_inference_allowance"])
        self.assertFalse(budget["enforced_dollar_cap"])
        self.assertIsNone(budget["all_in_total_usd"])
        self.assertTrue(all(cell["basis"] == "unknown" for cell in budget["cells"]))

    def test_budget_math_counts_every_repeat_and_avoids_subset_double_counting(self):
        _, design = self.generate(budget_brief())
        budget = design["budget"]
        self.assertEqual([cell["attempts"] for cell in budget["cells"]], [6, 6])
        self.assertAlmostEqual(budget["cells"][0]["estimated_usd_per_attempt"], 0.0035)
        self.assertAlmostEqual(budget["cells"][1]["estimated_usd_per_attempt"], 0.009)
        self.assertAlmostEqual(budget["estimated_inference_usd"], 0.075)
        self.assertAlmostEqual(budget["reserved_usd"], 0.025)
        self.assertAlmostEqual(budget["inference_allowance_usd"], 0.075)
        self.assertEqual(budget["estimated_attempts"], 12)
        self.assertEqual(budget["estimate_coverage"], 1)
        self.assertTrue(budget["within_inference_allowance"])
        self.assertFalse(budget["enforced_dollar_cap"])
        self.assertEqual(budget["cells"][0]["rate_card"], rate())

    def test_partial_pricing_preserves_coverage_subtotal_and_unknown_total(self):
        brief = budget_brief()
        brief["base_experiment"]["rate_cards"].pop()
        _, design = self.generate(brief)
        budget = design["budget"]
        self.assertIsNone(budget["estimated_inference_usd"])
        self.assertAlmostEqual(budget["known_estimate_subtotal_usd"], 0.021)
        self.assertEqual(budget["estimated_attempts"], 6)
        self.assertEqual(budget["estimate_coverage"], 0.5)
        self.assertIsNone(budget["within_inference_allowance"])
        self.assertIsNone(budget["cells"][1]["rate_card"])

    def test_exceeded_budget_keeps_exact_requested_attempts_and_targets(self):
        brief = budget_brief()
        brief["budget"].update(max_usd=0.05, reserve_fraction=0.2)
        path, design = self.generate(brief)
        self.assertFalse(design["budget"]["within_inference_allowance"])
        self.assertIn("estimated_budget_exceeded", {item["code"] for item in design["limitations"]})
        self.assertEqual(design["counts"]["total_attempts"], 12)
        self.assertEqual(load_experiment(path.parent / "experiment.json").repetitions, 2)
        self.assertEqual([t.model for t in load_experiment(path.parent / "experiment.json").targets],
                         ["chosen/model:exact", "chosen/model:other"])

    def test_missing_cache_assumptions_rates_and_price_bands_remain_unknown(self):
        for change in ("cache-assumption", "cache-rate", "price-band", "region", "service-tier", "provider"):
            with self.subTest(change=change):
                brief = budget_brief()
                if change == "cache-assumption":
                    del brief["budget"]["usage_per_attempt"][0]["cached_input_tokens"]
                elif change == "cache-rate":
                    del brief["base_experiment"]["rate_cards"][0]["cached_input_usd_per_million"]
                elif change == "price-band":
                    brief["base_experiment"]["rate_cards"][0]["max_input_tokens"] = 900
                else:
                    field = {"service-tier": "service_tier"}.get(change, change)
                    brief["base_experiment"]["rate_cards"][0][field] = "another-identity"
                _, design = self.generate(brief, output_name=change)
                self.assertIsNone(design["budget"]["cells"][0]["estimated_inference_usd"])
                self.assertEqual(design["budget"]["estimate_coverage"], 0.5)
                self.assertIsNone(design["budget"]["estimated_inference_usd"])

    def test_explicit_zero_rates_are_a_known_estimate(self):
        brief = budget_brief()
        for card in brief["base_experiment"]["rate_cards"]:
            for key in card:
                if key.endswith("_usd_per_million"):
                    card[key] = 0
        brief["budget"].update(max_usd=0, reserve_fraction=1)
        _, design = self.generate(brief)
        self.assertEqual(design["budget"]["estimated_inference_usd"], 0)
        self.assertEqual(design["budget"]["estimate_coverage"], 1)
        self.assertTrue(design["budget"]["within_inference_allowance"])

    def test_system_comparison_is_explicit_and_still_uses_one_task_protocol(self):
        brief = budget_brief()
        brief["conditions"][1]["target_changes"]["fixture"]["reasoning_effort"] = "high"
        source = self.root / "brief.json"
        source.write_text(json.dumps(brief))
        with self.assertRaises(ValueError):
            write_design(source, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())
        brief["contrast"] = "system-comparison"
        _, design = self.generate(brief)
        self.assertEqual(design["varied_fields"], ["model", "reasoning_effort"])
        self.assertEqual(len({c["plan"]["protocol_hash"] for c in design["conditions"]}), 1)

    def test_invalid_briefs_fail_before_publishing(self):
        cases = []

        def case(path, value):
            brief = offline_brief()
            cursor = brief
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = value
            cases.append(brief)

        case(["schema_version"], True)
        case(["question"], "  ")
        case(["contrast"], "uncontrolled")
        case(["conditions"], [])
        case(["notes"], "not-an-array")
        case(["extra"], "unsupported")
        case(["base_experiment", "schema_version"], True)
        case(["base_experiment", "targets"], None)
        case(["base_experiment", "tasks"], ["invented-task"])
        case(["base_experiment", "tasks"], ["inference-triage", "inference-triage"])
        case(["base_experiment", "repetitions"], 1.5)
        case(["base_experiment", "seed"], True)
        case(["base_experiment", "limits"], [])
        case(["base_experiment", "limits", "timeout_seconds"], 0)
        case(["base_experiment", "repetitions"], MAX_ATTEMPTS)
        case(["conditions", 0, "target_changes"], {"fixture": {"model": "changed-baseline"}})
        case(["conditions", 1, "id"], "BASELINE")
        case(["conditions", 1, "id"], "../outside")
        case(["conditions", 1, "target_changes"], {})
        case(["conditions", 1, "target_changes"], {"unknown": {"model": "other"}})
        case(["conditions", 1, "target_changes", "fixture"], {"model": "fixture-efficient"})
        for field in ("id", "tasks", "limits", "seed", "repetitions", "tools_dir", "prompt", "skills"):
            case(["conditions", 1, "target_changes", "fixture", field], "unsupported")
        for index, brief in enumerate(cases):
            with self.subTest(index=index):
                source = self.root / f"bad-{index}.json"
                source.write_text(json.dumps(brief))
                output = self.root / f"bad-{index}"
                with self.assertRaises(ValueError):
                    write_design(source, output)
                self.assertFalse(output.exists())

    def test_duplicate_json_keys_nonfinite_numbers_and_malformed_json_fail(self):
        for index, raw in enumerate((
            '{"schema_version": 1, "schema_version": 1}',
            '{"budget": {"max_usd": NaN}}',
            '{"budget": {"max_usd": Infinity}}',
            "[]", "{", "\xff",
        )):
            with self.subTest(raw=raw):
                source = self.root / f"raw-{index}.json"
                source.write_bytes(raw.encode("latin-1"))
                with self.assertRaises(ValueError):
                    write_design(source, self.root / f"raw-{index}")

    def test_invalid_budget_fields_and_token_subsets_fail_without_artifacts(self):
        cases = []
        for changes in [
            {"max_usd": -1}, {"max_usd": True}, {"reserve_fraction": 1.1},
            {"reserve_fraction": "0.2"}, {"reserve_fraction": -0.1},
            {"usage_per_attempt": {}}, {"unknown": 1},
        ]:
            brief = budget_brief()
            brief["budget"].update(changes)
            cases.append(brief)
        for changes in [
            {"target": "not-a-base-target"}, {"condition": "not-a-condition"}, {"source": ""},
            {"cached_input_tokens": 950}, {"cache_write_input_tokens": 900},
            {"reasoning_output_tokens": 201}, {"input_tokens": 1.1},
            {"input_tokens": True}, {"output_tokens": -1}, {"made_up_tokens": 4},
        ]:
            brief = budget_brief()
            brief["budget"]["usage_per_attempt"][0].update(changes)
            cases.append(brief)
        duplicate = budget_brief()
        duplicate["budget"]["usage_per_attempt"].append(duplicate["budget"]["usage_per_attempt"][0])
        cases.append(duplicate)
        for index, brief in enumerate(cases):
            with self.subTest(index=index):
                source = self.root / f"budget-{index}.json"
                source.write_text(json.dumps(brief))
                output = self.root / f"budget-{index}"
                with self.assertRaises(ValueError):
                    write_design(source, output)
                self.assertFalse(output.exists())

    def test_duplicate_conditions_or_alias_targets_cannot_masquerade_as_new_cells(self):
        brief = offline_brief()
        duplicate = copy.deepcopy(brief["conditions"][1])
        duplicate["id"] = "same-settings"
        brief["conditions"].append(duplicate)
        with self.assertRaises(ValueError):
            self.generate(brief)
        brief = offline_brief()
        alias = copy.deepcopy(brief["base_experiment"]["targets"][0])
        alias["id"] = "alias"
        brief["base_experiment"]["targets"].append(alias)
        brief["conditions"][1]["target_changes"]["alias"] = {"model": "another-fixture"}
        with self.assertRaises(ValueError):
            self.generate(brief)

    def test_template_requires_real_choices_and_skill_support_is_not_bypassed(self):
        with self.assertRaises(ValueError):
            self.generate(json.loads((ASSETS / "aws-cdk-skills-ablation.brief.json").read_text()))
        with self.assertRaises(ValueError):
            self.generate(self.cdk_brief())
        self.skill("skills/aws-cdk")
        brief = self.cdk_brief()
        brief["base_experiment"].update(
            suite="aws-bench", tasks=["describe-cloudformation-stack-resources"],
            aws_environment="user-chosen-environment",
        )
        with self.assertRaises(ValueError):
            self.generate(brief)

    def test_symlinked_skill_content_is_rejected_by_the_execution_digest(self):
        skill = self.skill("skills/aws-cdk")
        outside = self.root / "outside.txt"
        outside.write_text("fixture data")
        (skill / "link.txt").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.generate(self.cdk_brief())
        self.assertFalse((self.root / "design").exists())

    def test_existing_outputs_inputs_and_symlinks_are_not_overwritten(self):
        path, _ = self.generate()
        before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
        source = self.root / "brief.json"
        with self.assertRaises(FileExistsError):
            write_design(source, path.parent)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        original = source.read_bytes()
        with self.assertRaises(FileExistsError):
            write_design(source, source)
        self.assertEqual(source.read_bytes(), original)
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(FileExistsError):
            write_design(source, empty)
        link = self.root / "dangling"
        link.symlink_to(self.root / "absent", target_is_directory=True)
        with self.assertRaises(FileExistsError):
            write_design(source, link)
        self.assertFalse((self.root / "absent").exists())

    def test_plan_cannot_mutate_input_skill_or_installed_plugin(self):
        self.skill("skills/aws-cdk")
        with self.assertRaises(ValueError):
            self.generate(self.cdk_brief(), output_name="skills/aws-cdk/new-plan")
        source = self.root / "brief.json"
        source.write_text(json.dumps(offline_brief()))
        with self.assertRaises(ValueError):
            write_design(source, PLUGIN / "skills/design-experiment/forbidden-plan")

    def test_standalone_installed_plugin_needs_no_neighboring_checkout(self):
        installed = self.root / "installed-plugin"
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__"))
        source = installed / "skills/design-experiment/assets/offline-demo.brief.json"
        program = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts')); "
            "from bedrock_bench.design import write_design; "
            "print(write_design(sys.argv[2], sys.argv[3]))"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", program, str(installed), str(source), str(self.root / "standalone")],
            cwd=self.root, capture_output=True, text=True, timeout=30, check=True,
        )
        path = Path(result.stdout.strip())
        self.assertTrue(path.is_file())
        design = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(design["counts"]["total_attempts"], 12)
        self.assertEqual(design["provenance"]["brief_path"], str(source))


if __name__ == "__main__":
    unittest.main()
