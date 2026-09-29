"""Analytical saved-results fixtures; no inference, credentials, or network."""

import copy
import csv
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
SKILL = PLUGIN / "skills/compare-experiments"
sys.path.insert(0, str(PLUGIN / "scripts"))

from bedrock_bench.config import fingerprint
from bedrock_bench.explorer import _target_key
from bedrock_bench.research_compare import OUTPUT_FILES, write_comparison


def fixture(name="paired_mixed_outcomes", *, run_id="analytical-fixture", repeats=None):
    """Expand the shipped, explicitly invented observations into the saved schema."""
    resource = json.loads((SKILL / "resources/analytical-fixtures.json").read_text())
    case = copy.deepcopy(resource["cases"][name])
    if repeats is not None:
        for sides in case["tasks"].values():
            for role, rows in sides.items():
                sides[role] = rows * repeats
        case["repetitions"] *= repeats
    targets = [{
        "id": role, "runner": "demo", "provider": "synthetic", "model": f"analytical-{role}",
        "region": None, "reasoning_effort": None, "service_tier": "default",
        "aws_profile": None, "routing": [],
    } for role in ("baseline", "candidate")]
    protocol = {
        "suite": "analytical-fixture", "seed": 42, "repetitions": case["repetitions"],
        "limits": {"timeout_seconds": 60},
        "sources": [{"name": task, "sha256": fingerprint(["invented-fixture", task])}
                    for task in sorted(case["tasks"])],
        "scoring_revision": "synthetic-boolean-v1",
    }
    run = {
        "schema_version": 1, "run_id": run_id, "name": "Synthetic analytical observations",
        "status": "completed", "synthetic": True, "validation_only": False,
        "protocol": protocol, "protocol_hash": fingerprint(protocol),
        "experiment": {
            "suite": protocol["suite"], "tasks": sorted(case["tasks"]), "targets": targets,
            "repetitions": case["repetitions"], "seed": 42, "limits": protocol["limits"],
            "rate_cards": [],
        },
        "cost_scope": "Invented inference costs for analytical checks only.",
        "attempts": [],
    }
    for task, sides in case["tasks"].items():
        for target in targets:
            for rep, values in enumerate(sides[target["id"]], 1):
                row = dict(zip(resource["columns"], values))
                row.update(
                    attempt_id=f"{target['id']}-{task}-r{rep}", task_id=task, repetition=rep,
                    target=copy.deepcopy(target), runner_version="analytical-fixture-v1",
                    accounting_complete=row["cost_usd"] is not None, cost_basis=["synthetic"],
                )
                run["attempts"].append(row)
    return run


def upstream_fixture(*, run_id="upstream-fixture", harness="harbor"):
    """Invented observations in execute_suite's saved schema; no harness execution."""
    run = fixture(run_id=run_id)
    run.update(synthetic=False, name="Invented upstream observations")
    suite = "terminal-bench" if harness == "harbor" else "aws-bench"
    run["experiment"].update(suite=suite, harness=harness)
    run["protocol"]["suite"] = suite
    run["protocol_hash"] = fingerprint(run["protocol"])
    for target in run["experiment"]["targets"]:
        target.update(runner="codex", provider="openai")
    for row in run["attempts"]:
        row["target"].update(runner="codex", provider="openai")
        row.update(runner_version=f"{harness}/0.23.0; codex/0.125.0", cost_basis=["provider_reported"])
        row["upstream"] = {
            "harness": harness, "result": "upstream/trial/result.json",
            "agent_info": {"name": "codex", "version": "0.125.0"},
            "task_checksum": "fixture-" + row["task_id"],
        }
    return run


def missing_upstream_version(row):
    row["runner_version"] = row["runner_version"].rsplit("/", 1)[0] + "/unavailable"
    row["upstream"].update(result=None, agent_info={}, task_checksum=None)
    row.update(status="timeout", success=False, agent_wall_seconds=None,
               cost_usd=None, known_cost_subtotal_usd=1.25, accounting_complete=False,
               cost_basis=["provider_reported", "unknown"])


def retask(run, tasks):
    run["experiment"]["tasks"] = list(tasks)
    run["protocol"]["sources"] = [s for s in run["protocol"]["sources"] if s["name"] in tasks]
    run["protocol_hash"] = fingerprint(run["protocol"])
    run["attempts"] = [row for row in run["attempts"] if row["task_id"] in tasks]


def only_target(run, target_id):
    run["experiment"]["targets"] = [t for t in run["experiment"]["targets"] if t["id"] == target_id]
    run["attempts"] = [row for row in run["attempts"] if row["target"]["id"] == target_id]


class ResearchComparisonTests(unittest.TestCase):
    def setUp(self):
        # The installed plugin may be read-only; fixtures belong in scratch space.
        self.temp = tempfile.TemporaryDirectory(prefix="bench-research-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.serial = 0

    def save(self, run, name=None):
        directory = self.directory / (name or run["run_id"])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "run.json"
        path.write_text(json.dumps(run, allow_nan=True))
        return path

    def analyze(self, sources, **options):
        self.serial += 1
        options.setdefault("baseline", "baseline")
        options.setdefault("candidate", "candidate")
        path = write_comparison(sources, self.directory / f"comparison-{self.serial}", **options)
        self.assertEqual(path.name, "COMPARISON.md")
        self.assertTrue(path.is_absolute())
        self.assertTrue(path.is_file())
        return json.loads(path.with_suffix(".json").read_text()), path

    def assert_rejected(self, run, **options):
        source = self.save(run)
        original = source.read_bytes()
        destination = self.directory / "rejected"
        with self.assertRaises(ValueError):
            write_comparison([source], destination, baseline="baseline", candidate="candidate", **options)
        self.assertFalse(destination.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_hand_calculated_paired_effects_and_failure_spend(self):
        source = self.save(fixture())
        original = source.read_bytes()
        data, path = self.analyze([source])
        self.assertEqual(data["evidence_type"], "synthetic")
        self.assertEqual(data["pairing"]["distinct_tasks"], 3)
        self.assertEqual(data["pairing"]["attempts_per_side"], 6)
        before, after = data["aggregates"]["baseline"], data["aggregates"]["candidate"]
        self.assertEqual((before["successes"], after["successes"]), (3, 4))
        self.assertEqual((before["total_cost_usd"], after["total_cost_usd"]), (10, 14))
        self.assertAlmostEqual(before["cost_per_success_usd"], 10 / 3)
        self.assertAlmostEqual(after["cost_per_success_usd"], 14 / 4)
        expected = {
            "success_rate": 1 / 6, "mean_wall_seconds": -1 / 3,
            "mean_agent_seconds": -1 / 6, "cost_per_attempt_usd": 2 / 3,
            "cost_per_success_usd": 1 / 6,
        }
        for name, delta in expected.items():
            self.assertAlmostEqual(data["metrics"][name]["delta"], delta, msg=name)
        alpha, beta, gamma = data["tasks"]
        self.assertEqual([t["task_id"] for t in data["tasks"]], ["alpha", "beta", "gamma"])
        self.assertEqual([t["deltas"]["success_rate"] for t in data["tasks"]], [.5, -.5, .5])
        self.assertEqual([t["deltas"]["mean_wall_seconds"] for t in data["tasks"]], [-5, 4, 0])
        self.assertIsNone(gamma["baseline"]["cost_per_success_usd"])
        self.assertEqual(after["failures"], {"timeout": 1, "incomplete": 1})
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(data["sources"][0]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(data["sources"][0]["path"], str(source))
        self.assertEqual(set(p.name for p in path.parent.iterdir()), set(OUTPUT_FILES))

    def test_unknown_spend_keeps_subtotals_and_disables_full_cost_effects(self):
        run = fixture()
        for row in run["attempts"]:
            if row["target"]["id"] == "candidate" and row["status"] == "timeout":
                row.update(cost_usd=None, known_cost_subtotal_usd=1.25,
                           accounting_complete=False, cost_basis=["synthetic", "unknown"])
        data, path = self.analyze([self.save(run)])
        candidate = data["aggregates"]["candidate"]
        self.assertIsNone(candidate["total_cost_usd"])
        self.assertIsNone(candidate["cost_per_success_usd"])
        self.assertAlmostEqual(candidate["known_cost_subtotal_usd"], 10.25)
        self.assertAlmostEqual(candidate["cost_coverage"], 5 / 6)
        self.assertIn("unknown", candidate["cost_basis"])
        for key in ("cost_per_attempt_usd", "cost_per_success_usd"):
            self.assertIsNone(data["metrics"][key]["delta"])
            self.assertIsNone(data["metrics"][key]["confidence_interval"])
        self.assertAlmostEqual(data["metrics"]["success_rate"]["delta"], 1 / 6)
        with (path.parent / "ATTEMPTS.csv").open(newline="") as stream:
            timeout = next(row for row in csv.DictReader(stream) if row["status"] == "timeout")
        self.assertEqual(timeout["cost_usd"], "")
        self.assertEqual(float(timeout["known_cost_subtotal_usd"]), 1.25)

    def test_zero_successes_is_undefined_even_with_known_zero_cost(self):
        run = fixture("opposing_tasks")
        for row in run["attempts"]:
            if row["target"]["id"] == "baseline":
                row.update(success=False, status="task_failed", cost_usd=0, known_cost_subtotal_usd=0)
        data, _ = self.analyze([self.save(run)])
        self.assertEqual(data["aggregates"]["baseline"]["total_cost_usd"], 0)
        self.assertIsNone(data["aggregates"]["baseline"]["cost_per_success_usd"])
        self.assertIsNone(data["metrics"]["cost_per_success_usd"]["delta"])
        self.assertIsNone(data["metrics"]["cost_per_success_usd"]["confidence_interval"])

    def test_repetition_does_not_masquerade_as_more_independent_tasks(self):
        one = fixture("opposing_tasks", run_id="two-tasks-once")
        many = fixture("opposing_tasks", run_id="two-tasks-many", repeats=50)
        first, _ = self.analyze([self.save(one)], seed=51)
        repeated, _ = self.analyze([self.save(many)], seed=51)
        for key in first["metrics"]:
            self.assertEqual(first["metrics"][key], repeated["metrics"][key], key)
        interval = first["metrics"]["success_rate"]["confidence_interval"]
        self.assertEqual((interval["low"], interval["high"]), (-1, 1))
        self.assertEqual(repeated["pairing"]["distinct_tasks"], 2)
        self.assertEqual(repeated["pairing"]["attempts_per_side"], 100)

    def test_additional_distinct_tasks_change_uncertainty(self):
        run = fixture("opposing_tasks")
        expanded = copy.deepcopy(run)
        expanded["run_id"] = "ten-distinct-tasks"
        expanded["experiment"]["tasks"] = []
        expanded["protocol"]["sources"] = []
        expanded["attempts"] = []
        for index in range(5):
            for source in run["protocol"]["sources"]:
                task = source["name"] + f"-{index}"
                expanded["experiment"]["tasks"].append(task)
                expanded["protocol"]["sources"].append({"name": task, "sha256": fingerprint(task)})
            for row in run["attempts"]:
                row = copy.deepcopy(row)
                row["task_id"] += f"-{index}"
                row["attempt_id"] += f"-{index}"
                expanded["attempts"].append(row)
        expanded["protocol_hash"] = fingerprint(expanded["protocol"])
        data, _ = self.analyze([self.save(expanded)])
        interval = data["metrics"]["success_rate"]["confidence_interval"]
        self.assertEqual(data["pairing"]["distinct_tasks"], 10)
        self.assertEqual(data["metrics"]["success_rate"]["delta"], 0)
        self.assertLess(interval["high"] - interval["low"], 2)
        self.assertLessEqual(interval["low"], 0)
        self.assertGreaterEqual(interval["high"], 0)

    def test_one_task_with_many_repeats_has_no_confidence_interval(self):
        run = fixture("opposing_tasks", repeats=40)
        retask(run, ["candidate_win"])
        with patch("bedrock_bench.research_compare.random.Random",
                   side_effect=AssertionError("A single task must skip bootstrap sampling")):
            data, path = self.analyze([self.save(run)])
        self.assertEqual(data["pairing"]["distinct_tasks"], 1)
        self.assertEqual(data["pairing"]["attempts_per_side"], 40)
        self.assertEqual(data["metrics"]["success_rate"]["delta"], 1)
        self.assertEqual(data["method"]["resamples"], 2000)
        self.assertEqual(data["method"]["configured_resamples"], 2000)
        self.assertEqual(data["method"]["performed_resamples"], 0)
        for value in data["metrics"].values():
            self.assertIsNone(value["confidence_interval"])
            self.assertEqual(value["valid_resamples"], 0)
            self.assertEqual(value["valid_resamples"] + value["undefined_resamples"],
                             data["method"]["performed_resamples"])
        self.assertEqual(data["metrics"]["success_rate"]["interval_unavailable_reason"], "one_distinct_task")
        self.assertIn("2000 configured draws; 0 performed", path.read_text())

    def test_bootstrap_metadata_counts_undefined_draws_as_performed(self):
        run = fixture("opposing_tasks")
        row = run["attempts"][0]
        row.update(cost_usd=None, accounting_complete=False)
        source = self.save(run)
        data, path = self.analyze([source], resamples=173)
        method = data["method"]
        self.assertEqual(method["configured_resamples"], 173)
        self.assertEqual(method["resamples"], method["configured_resamples"])
        self.assertEqual(method["performed_resamples"], 173)
        for value in data["metrics"].values():
            self.assertEqual(value["valid_resamples"] + value["undefined_resamples"],
                             method["performed_resamples"])
        self.assertGreater(data["metrics"]["cost_per_attempt_usd"]["undefined_resamples"], 0)
        self.assertEqual(data["metrics"]["success_rate"]["valid_resamples"], 173)
        self.assertIn("173 configured draws; 173 performed", path.read_text())

        retask(run, ["candidate_win"])
        single, _ = self.analyze([self.save(run)], resamples=173)
        self.assertEqual(single["method"]["configured_resamples"], 173)
        self.assertEqual(single["method"]["performed_resamples"], 0)
        self.assertNotIn("few_resamples", {item["code"] for item in single["limitations"]})

    def test_pairing_retains_shared_task_difficulty(self):
        run = fixture("opposing_tasks")
        for row in run["attempts"]:
            row["success"] = row["task_id"] == "candidate_win"
            row["status"] = "completed" if row["success"] else "task_failed"
        data, _ = self.analyze([self.save(run)])
        success = data["metrics"]["success_rate"]
        self.assertEqual(success["delta"], 0)
        self.assertEqual(success["confidence_interval"]["low"], 0)
        self.assertEqual(success["confidence_interval"]["high"], 0)

    def test_seeded_bootstrap_matches_independently_calculated_task_deltas(self):
        data, _ = self.analyze([self.save(fixture())], resamples=173, seed=19)
        rng = random.Random(19)
        deltas = [.5, -.5, .5]
        samples = sorted(sum(deltas[rng.randrange(3)] for _ in range(3)) / 3 for _ in range(173))
        expected_low = samples[4] * .7 + samples[5] * .3  # .025 * 172 = 4.3
        expected_high = samples[167] * .3 + samples[168] * .7  # .975 * 172 = 167.7
        interval = data["metrics"]["success_rate"]["confidence_interval"]
        self.assertAlmostEqual(interval["low"], expected_low)
        self.assertAlmostEqual(interval["high"], expected_high)
        self.assertEqual(data["metrics"]["success_rate"]["valid_resamples"], 173)
        other, _ = self.analyze([self.save(fixture())], resamples=173, seed=20)
        self.assertNotEqual(data["method"]["seed"], other["method"]["seed"])
        # Tiny resampling runs expose a real seed-dependent sampled distribution.
        endpoints = set()
        for seed in range(8):
            sample, _ = self.analyze([self.save(fixture())], resamples=2, seed=seed)
            ci = sample["metrics"]["mean_wall_seconds"]["confidence_interval"]
            endpoints.add((ci["low"], ci["high"]))
        self.assertGreater(len(endpoints), 1)

    def test_undefined_ratio_draws_are_counted_not_filtered_into_a_ci(self):
        run = fixture("opposing_tasks")
        data, _ = self.analyze([self.save(run)], resamples=200, seed=31)
        ratio = data["metrics"]["cost_per_success_usd"]
        self.assertEqual(ratio["delta"], 2)
        # Each side fails on one of two tasks. Only mixed draws define both ratios.
        rng = random.Random(31)
        valid = sum(rng.randrange(2) != rng.randrange(2) for _ in range(200))
        self.assertEqual(ratio["valid_resamples"], valid)
        self.assertEqual(ratio["undefined_resamples"], 200 - valid)
        self.assertIsNone(ratio["confidence_interval"])
        self.assertEqual(ratio["interval_unavailable_reason"], "undefined_bootstrap_draws")

    def test_curves_include_failures_timeouts_and_separate_wall_from_agent(self):
        data, path = self.analyze([self.save(fixture())])
        wall = data["success_by_budget"]["wall_seconds"]
        agent = data["success_by_budget"]["agent_wall_seconds"]
        self.assertTrue(wall["descriptive"])
        self.assertEqual(wall["missing_attempts"], {"baseline": 0, "candidate": 0})
        points = {(row["role"], row["budget_seconds"]): row for row in wall["points"]}
        final = points["candidate", 40]
        self.assertEqual((final["attempts"], final["ended_attempts"], final["successful_attempts"],
                          final["failed_ended_attempts"]), (6, 6, 4, 2))
        self.assertAlmostEqual(final["success_fraction"], 4 / 6)
        wall_at_ten = points["baseline", 10]["success_fraction"]
        agent_at_ten = next(row["success_fraction"] for row in agent["points"]
                            if row["role"] == "baseline" and row["budget_seconds"] == 10)
        self.assertAlmostEqual(wall_at_ten, 1 / 6)
        self.assertAlmostEqual(agent_at_ten, 3 / 6)
        with (path.parent / "ATTEMPTS.csv").open(newline="") as stream:
            attempts = list(csv.DictReader(stream))
        self.assertEqual(len(attempts), 12)
        self.assertEqual(sum(row["success"] == "False" for row in attempts), 5)
        self.assertTrue(any(row["status"] == "incomplete" for row in attempts))
        with (path.parent / "TASKS.csv").open(newline="") as stream:
            tasks = list(csv.DictReader(stream))
        self.assertAlmostEqual(sum(float(t["delta_success_rate"]) for t in tasks) / 3, 1 / 6)

    def test_missing_agent_time_does_not_drop_a_failed_attempt(self):
        run = fixture()
        row = next(r for r in run["attempts"] if r["status"] == "timeout")
        row["agent_wall_seconds"] = None
        data, _ = self.analyze([self.save(run)])
        self.assertIsNone(data["metrics"]["mean_agent_seconds"]["delta"])
        self.assertEqual(data["success_by_budget"]["agent_wall_seconds"]["status"], "unavailable")
        self.assertFalse(data["success_by_budget"]["agent_wall_seconds"]["points"])
        self.assertEqual(data["success_by_budget"]["agent_wall_seconds"]["missing_attempts"]["candidate"], 1)
        self.assertTrue(data["success_by_budget"]["wall_seconds"]["points"])
        self.assertEqual(len(data["attempts"]), 12)

    def test_duplicates_run_ids_attempt_ids_and_logical_slots_are_rejected(self):
        run = fixture()
        first = self.save(run)
        duplicate = self.save(run, "archived-copy")
        with self.assertRaises(ValueError):
            self.analyze([first, duplicate])
        for duplicate_id in (True, False):
            with self.subTest(duplicate_id=duplicate_id):
                changed = copy.deepcopy(run)
                row = copy.deepcopy(changed["attempts"][0])
                if not duplicate_id:
                    row["attempt_id"] += "-different-label"
                changed["attempts"].append(row)
                self.assert_rejected(changed)

    def test_missing_or_extra_schedule_cells_are_never_dropped(self):
        mutations = [
            lambda run: run["attempts"].pop(),
            lambda run: run["attempts"][0].update(task_id="unmatched"),
            lambda run: run["attempts"][0].update(repetition=3),
            lambda run: run.update(status="interrupted"),
            lambda run: run.update(status="running"),
            lambda run: run["experiment"]["tasks"].append("absent"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                run = fixture()
                mutate(run)
                self.assert_rejected(run)
        run = fixture()
        # Balanced deletion on both sides is still an incomplete declared schedule.
        run["attempts"] = [r for r in run["attempts"] if r["task_id"] != "gamma"]
        self.assert_rejected(run)

    def test_multiple_saved_runs_require_balanced_cells_without_arbitrary_pairing(self):
        runs = []
        for role, suffix in (("baseline", "1"), ("baseline", "2"), ("candidate", "1")):
            run = fixture(run_id=f"{role}-{suffix}")
            only_target(run, role)
            runs.append(self.save(run))
        with self.assertRaises(ValueError):
            self.analyze(runs)
        run = fixture(run_id="candidate-2")
        only_target(run, "candidate")
        runs.append(self.save(run))
        data, _ = self.analyze(runs)
        self.assertEqual(data["pairing"]["observations_per_stratum_per_side"], [2])
        self.assertEqual(data["pairing"]["attempts_per_side"], 12)
        self.assertAlmostEqual(data["metrics"]["success_rate"]["delta"], 1 / 6)
        # Attempt IDs legitimately repeat in distinct runs; provenance distinguishes them.
        self.assertEqual(len(data["attempts"]), 24)
        self.assertLess(len({r["attempt_id"] for r in data["attempts"]}), 24)

    def test_different_protocols_and_disallowed_controlled_factors_fail(self):
        original = fixture()
        for field, value in (("seed", 99), ("limits", {"timeout_seconds": 120})):
            with self.subTest(field=field):
                altered = copy.deepcopy(original)
                altered["run_id"] = f"changed-{field}"
                altered["experiment"][field] = value
                altered["protocol"][field] = value
                altered["protocol_hash"] = fingerprint(altered["protocol"])
                with self.assertRaises(ValueError):
                    self.analyze([self.save(original), self.save(altered)])
        altered = copy.deepcopy(original)
        altered["run_id"] = "new-fixture"
        altered["protocol"]["sources"][0]["sha256"] = "different-content"
        altered["protocol_hash"] = fingerprint(altered["protocol"])
        with self.assertRaises(ValueError):
            self.analyze([self.save(original), self.save(altered)])
        altered["protocol_hash"] = original["protocol_hash"]  # A stale hash cannot hide the change.
        self.assert_rejected(altered)

    def test_legacy_hash_only_metadata_cannot_hide_changed_settings(self):
        original = fixture()
        original.pop("protocol")
        altered = copy.deepcopy(original)
        altered.update(run_id="changed-recorded-seed")
        altered["experiment"]["seed"] = 900
        with self.assertRaises(ValueError):
            self.analyze([self.save(original), self.save(altered)])
        valid, _ = self.analyze([self.save(original)])
        self.assertEqual(valid["pairing"]["distinct_tasks"], 3)
        self.assertIsNone(valid["protocol"])

    def test_synthetic_reference_and_live_are_separate_evidence_types(self):
        original = fixture()
        for evidence_type in ("live", "reference"):
            altered = copy.deepcopy(original)
            altered.update(run_id=evidence_type, synthetic=False, validation_only=evidence_type == "reference")
            for target in altered["experiment"]["targets"]:
                target.update(runner="native" if evidence_type == "live" else "oracle",
                              provider="openai" if evidence_type == "live" else "reference")
            for row in altered["attempts"]:
                row["target"] = copy.deepcopy(next(t for t in altered["experiment"]["targets"]
                                                   if t["id"] == row["target"]["id"]))
                row["cost_basis"] = ["provider_reported" if evidence_type == "live" else "reference_no_model"]
            with self.subTest(evidence_type=evidence_type), self.assertRaises(ValueError):
                self.analyze([self.save(original), self.save(altered)])
        mislabeled = fixture()
        mislabeled.update(synthetic=False)
        self.assert_rejected(mislabeled)

    def test_known_fixture_checksum_conflicts_fail_missing_ones_remain_visible(self):
        run = fixture()
        for row in run["attempts"]:
            row["upstream"] = {"task_checksum": "fixture-" + row["task_id"]}
        run["attempts"][0]["upstream"]["task_checksum"] = "changed"
        self.assert_rejected(run)
        run["attempts"][0]["upstream"]["task_checksum"] = None
        data, _ = self.analyze([self.save(run)])
        self.assertEqual(len(data["attempts"]), 12)
        first = data["tasks"][0]["fixtures"][0]
        self.assertEqual(first["checksum_observations"], 1)
        self.assertEqual(first["observations"], 2)
        self.assertIsNotNone(first["protocol_source"])

    def test_pinned_upstream_commit_cannot_be_changed_under_an_unchanged_hash(self):
        run = fixture()
        run["protocol"]["sources"][0]["git_commit_id"] = "saved-commit"
        run["protocol_hash"] = fingerprint(run["protocol"])
        run["attempts"][0]["upstream"] = {"task_id": {"git_commit_id": "other-commit"}}
        self.assert_rejected(run)

    def test_missing_upstream_version_keeps_timeout_for_target_and_exact_selectors(self):
        for harness in ("harbor", "aws-bench"):
            run = upstream_fixture(harness=harness)
            timeout = next(row for row in run["attempts"] if row["status"] == "timeout")
            version = timeout["runner_version"]
            key = _target_key(timeout["target"], version)
            missing_upstream_version(timeout)
            recorded_key = _target_key(timeout["target"], timeout["runner_version"])
            source = self.save(run)
            original = source.read_bytes()
            for selector in ("candidate", key, {"target_id": "candidate", "identity_key": key,
                                               "run_ids": [run["run_id"]]}):
                with self.subTest(harness=harness, selector=selector):
                    data, path = self.analyze([source], candidate=selector)
                    selection = data["selections"]["candidate"]
                    self.assertEqual(selection["identity_key"], key)
                    self.assertEqual(selection["identity"]["runner_version"], version)
                    self.assertEqual(selection["runner_version_evidence"], {
                        "known_versions": [version], "known_attempts": 5, "missing_attempts": 1,
                    })
                    self.assertEqual(len(data["attempts"]), 12)
                    self.assertEqual(data["pairing"]["attempts_per_side"], 6)
                    self.assertEqual(data["pairing"]["excluded"], [])
                    candidate = data["aggregates"]["candidate"]
                    self.assertEqual(candidate["successes"], 4)
                    self.assertEqual(candidate["failures"], {"timeout": 1, "incomplete": 1})
                    self.assertIsNone(candidate["total_cost_usd"])
                    self.assertAlmostEqual(candidate["known_cost_subtotal_usd"], 10.25)
                    self.assertAlmostEqual(candidate["cost_coverage"], 5 / 6)
                    self.assertIsNone(data["metrics"]["cost_per_attempt_usd"]["delta"])
                    self.assertIsNone(data["metrics"]["cost_per_success_usd"]["delta"])
                    self.assertAlmostEqual(data["metrics"]["success_rate"]["delta"], 1 / 6)
                    saved = next(row for row in data["attempts"] if row["attempt_id"] == timeout["attempt_id"])
                    self.assertEqual(saved["runner_version"], timeout["runner_version"])
                    self.assertEqual(saved["recorded_identity_key"], recorded_key)
                    self.assertEqual(saved["identity_key"], key)
                    self.assertTrue(saved["runner_version_missing"])
                    final = data["success_by_budget"]["wall_seconds"]["points"][-1]
                    self.assertEqual((final["role"], final["attempts"], final["failed_ended_attempts"]),
                                     ("candidate", 6, 2))
                    with (path.parent / "ATTEMPTS.csv").open(newline="") as stream:
                        exported = next(row for row in csv.DictReader(stream) if row["status"] == "timeout")
                    self.assertEqual(exported["runner_version"], timeout["runner_version"])
                    self.assertEqual(exported["recorded_identity_key"], recorded_key)
                    self.assertEqual(exported["cost_usd"], "")
                    self.assertEqual(float(exported["known_cost_subtotal_usd"]), 1.25)
                    self.assertIn("missing_runner_version", {item["code"] for item in data["limitations"]})
                    self.assertEqual(source.read_bytes(), original)

    def test_missing_upstream_version_keeps_known_failure_spend(self):
        run = upstream_fixture()
        row = next(row for row in run["attempts"] if row["status"] == "timeout")
        missing_upstream_version(row)
        row.update(cost_usd=5, known_cost_subtotal_usd=5, accounting_complete=True,
                   cost_basis=["provider_reported"])
        data, _ = self.analyze([self.save(run)])
        candidate = data["aggregates"]["candidate"]
        self.assertEqual(candidate["attempts"], 6)
        self.assertEqual(candidate["total_cost_usd"], 14)
        self.assertEqual(candidate["known_cost_subtotal_usd"], 14)
        self.assertEqual(candidate["cost_coverage"], 1)
        self.assertEqual(candidate["cost_per_success_usd"], 3.5)
        self.assertAlmostEqual(data["metrics"]["cost_per_success_usd"]["delta"], 1 / 6)

    def test_resolving_missing_versions_preserves_duplicate_identity_slot_gate(self):
        run = upstream_fixture()
        for target in run["experiment"]["targets"]:
            target["model"] = "same-model"
        for row in run["attempts"]:
            row["target"]["model"] = "same-model"
            if (row["target"]["id"] == "baseline") == (row["repetition"] == 1):
                missing_upstream_version(row)
        with self.assertRaisesRegex(ValueError, "Duplicate attempt task/repetition slot"):
            self.analyze([self.save(run)])

    def test_all_missing_upstream_versions_keep_unknown_identity_and_failed_attempts(self):
        run = upstream_fixture()
        for row in run["attempts"]:
            missing_upstream_version(row)
        source = self.save(run)
        candidate = next(row for row in run["attempts"] if row["target"]["id"] == "candidate")
        for selector in ("candidate", _target_key(candidate["target"], candidate["runner_version"])):
            with self.subTest(selector=selector):
                data, _ = self.analyze([source], candidate=selector)
                self.assertEqual(len(data["attempts"]), 12)
                self.assertEqual(data["pairing"]["excluded"], [])
                for role in ("baseline", "candidate"):
                    selection = data["selections"][role]
                    self.assertEqual(selection["identity"]["runner_version"], "harbor/0.23.0; codex/unavailable")
                    self.assertEqual(selection["runner_version_evidence"], {
                        "known_versions": [], "known_attempts": 0, "missing_attempts": 6,
                    })
                    aggregate = data["aggregates"][role]
                    self.assertEqual(aggregate["failures"], {"timeout": 6})
                    self.assertIsNone(aggregate["total_cost_usd"])
                    self.assertEqual(aggregate["known_cost_subtotal_usd"], 7.5)
                    self.assertEqual(aggregate["cost_coverage"], 0)
                self.assertTrue(all(row["runner_version_missing"] for row in data["attempts"]))
                self.assertIn("missing_runner_version", {item["code"] for item in data["limitations"]})

    def test_missing_upstream_version_does_not_hide_real_version_changes(self):
        for status in ("completed", "runner_error"):
            run = upstream_fixture()
            timeout = next(row for row in run["attempts"] if row["status"] == "timeout")
            original_key = _target_key(timeout["target"], timeout["runner_version"])
            missing_upstream_version(timeout)
            changed = next(row for row in run["attempts"]
                           if row["target"]["id"] == "candidate" and row["success"])
            changed.update(runner_version="harbor/0.23.0; codex/0.126.0",
                           status=status, success=status == "completed")
            changed["upstream"]["agent_info"]["version"] = "0.126.0"
            changed_key = _target_key(changed["target"], changed["runner_version"])
            source = self.save(run)
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "Ambiguous candidate selector"):
                    self.analyze([source])
                for key in (original_key, changed_key):
                    with self.assertRaisesRegex(ValueError, "incomplete task/repetition panel"):
                        self.analyze([source], candidate=key)

    def test_arbitrary_versions_and_recorded_version_evidence_are_not_missing(self):
        for version, info in (
            ("harbor/0.23.0; codex/unknown", {}),
            ("harbor/0.23.0; codex/dev-unavailable", {}),
            ("unavailable", {}),
            ("codex/unavailable", {}),
            ("harbor/0.23.0; codex/unavailable", {"version": "unavailable"}),
            ("harbor/0.23.0; codex/unavailable", {"version": "0.126.0"}),
            ("harbor/0.23.0; codex/unavailable", None),
        ):
            run = upstream_fixture()
            row = next(row for row in run["attempts"] if row["status"] == "timeout")
            key = _target_key(row["target"], row["runner_version"])
            missing_upstream_version(row)
            row["runner_version"] = version
            row["upstream"]["agent_info"] = info
            source = self.save(run)
            with self.subTest(version=version, info=info):
                with self.assertRaisesRegex(ValueError, "Ambiguous candidate selector"):
                    self.analyze([source])
                with self.assertRaisesRegex(ValueError, "incomplete task/repetition panel"):
                    self.analyze([source], candidate=key)

    def test_missing_version_in_saved_trial_is_not_limited_to_timeout_status(self):
        for status in ("runner_error", "incomplete", "task_failed", "completed"):
            run = upstream_fixture()
            row = next(row for row in run["attempts"] if row["status"] == "timeout")
            missing_upstream_version(row)
            row.update(status=status, success=status == "completed")
            row["upstream"].update(result="upstream/trial/result.json",
                                   agent_info={"name": "codex", "version": None})
            with self.subTest(status=status):
                data, _ = self.analyze([self.save(run)])
                self.assertEqual(data["selections"]["candidate"]["attempts"], 6)
                self.assertEqual(data["selections"]["candidate"]["runner_version_evidence"]["missing_attempts"], 1)

    def test_missing_versions_do_not_borrow_evidence_from_other_runs_or_target_ids(self):
        known, missing = upstream_fixture(run_id="known"), upstream_fixture(run_id="missing")
        for row in missing["attempts"]:
            missing_upstream_version(row)
        sources = [self.save(known), self.save(missing)]
        with self.assertRaisesRegex(ValueError, "Ambiguous baseline selector"):
            self.analyze(sources)
        data, _ = self.analyze(sources,
                               baseline={"target_id": "baseline", "run_ids": ["missing"]},
                               candidate={"target_id": "candidate", "run_ids": ["missing"]})
        self.assertEqual(data["selections"]["candidate"]["runner_version_evidence"]["known_versions"], [])
        run = upstream_fixture()
        for target in run["experiment"]["targets"]:
            target["model"] = "same-model"
        for row in run["attempts"]:
            row["target"]["model"] = "same-model"
            if row["target"]["id"] == "candidate":
                missing_upstream_version(row)
        data, _ = self.analyze([self.save(run)])
        self.assertEqual(data["selections"]["candidate"]["runner_version_evidence"]["known_versions"], [])
        self.assertNotEqual(data["selections"]["baseline"]["identity_key"],
                            data["selections"]["candidate"]["identity_key"])

    def test_missing_versions_do_not_erase_harness_or_observed_settings_changes(self):
        for change in ("harness", "settings"):
            run = upstream_fixture()
            row = next(row for row in run["attempts"] if row["status"] == "timeout")
            key = _target_key(row["target"], row["runner_version"])
            missing_upstream_version(row)
            if change == "harness":
                row["runner_version"] = "harbor/0.24.0; codex/unavailable"
            else:
                row["target"]["skill_sha256"] = {"fixture-skill": "changed"}
            source = self.save(run)
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, "Ambiguous candidate selector"):
                    self.analyze([source])
                with self.assertRaisesRegex(ValueError, "incomplete task/repetition panel"):
                    self.analyze([source], candidate=key)

    def test_target_id_ambiguity_exact_keys_and_explicit_run_selection(self):
        original = fixture()
        changed = fixture(run_id="new-version")
        for row in changed["attempts"]:
            if row["target"]["id"] == "baseline":
                row["runner_version"] = "analytical-fixture-v2"
        sources = [self.save(original), self.save(changed)]
        with self.assertRaises(ValueError):
            self.analyze(sources)
        key = _target_key(original["attempts"][0]["target"], original["attempts"][0]["runner_version"])
        data, _ = self.analyze(
            sources, baseline=key, candidate={"target_id": "candidate", "run_ids": [original["run_id"]]},
        )
        self.assertEqual(data["selections"]["baseline"]["identity_key"], key)
        self.assertEqual(data["selections"]["baseline"]["identity"]["runner_version"], "analytical-fixture-v1")
        self.assertEqual(sum(row["attempts"] for row in data["pairing"]["excluded"]), 12)
        data, _ = self.analyze(sources, baseline={"target_id": "baseline", "run_ids": ["new-version"]},
                               candidate={"target_id": "candidate", "run_ids": ["new-version"]})
        self.assertEqual(data["selections"]["baseline"]["identity"]["runner_version"], "analytical-fixture-v2")

    def test_settings_are_part_of_execution_identity(self):
        first = fixture()
        second = fixture(run_id="different-settings")
        for target in second["experiment"]["targets"]:
            if target["id"] == "baseline":
                target["reasoning_effort"] = "high"
        for row in second["attempts"]:
            if row["target"]["id"] == "baseline":
                row["target"]["reasoning_effort"] = "high"
        with self.assertRaises(ValueError):
            self.analyze([self.save(first), self.save(second)])
        data, _ = self.analyze([self.save(second)])
        self.assertEqual(data["selections"]["baseline"]["identity"]["reasoning_effort"], "high")
        self.assertTrue(any(d["field"] == "reasoning_effort" for d in data["identity_differences"]))

    def test_same_identity_can_compare_disjoint_saved_runs_but_not_itself(self):
        first = fixture()
        second = fixture(run_id="later-run")
        for row in second["attempts"]:
            if row["target"]["id"] == "baseline":
                row.update(success=True, status="completed")
        sources = [self.save(first), self.save(second)]
        data, _ = self.analyze(sources,
                               baseline={"target_id": "baseline", "run_ids": [first["run_id"]]},
                               candidate={"target_id": "baseline", "run_ids": [second["run_id"]]})
        self.assertEqual(data["selections"]["baseline"]["identity_key"],
                         data["selections"]["candidate"]["identity_key"])
        self.assertAlmostEqual(data["metrics"]["success_rate"]["delta"], .5)
        self.assertEqual(data["identity_differences"], [])
        with self.assertRaises(ValueError):
            self.analyze(sources, baseline="baseline", candidate="baseline")

    def test_exact_selector_cannot_hide_a_partial_version_panel(self):
        run = fixture()
        row = run["attempts"][0]
        key = _target_key(row["target"], row["runner_version"])
        row["runner_version"] = "different-version"
        with self.assertRaises(ValueError):
            self.analyze([self.save(run)], baseline=key)
        with self.assertRaises(ValueError):
            self.analyze([self.save(run)], baseline={"target_id": "baseline", "run_ids": ["not-supplied"]})

    def test_numeric_validation_and_arithmetic_overflow_never_write_nan(self):
        for field in ("wall_seconds", "agent_wall_seconds", "cost_usd", "known_cost_subtotal_usd"):
            for value in (float("nan"), float("inf"), -float("inf"), -1, True, "1.0"):
                with self.subTest(field=field, value=value):
                    run = fixture()
                    run["attempts"][0][field] = value
                    self.assert_rejected(run)
        for field, value in (("success", 1), ("repetition", 1.0), ("accounting_complete", 1)):
            run = fixture()
            run["attempts"][0][field] = value
            self.assert_rejected(run)
        source = self.save(fixture())
        source.write_text(source.read_text().replace('"wall_seconds": 10', '"wall_seconds": 1e999', 1))
        with self.assertRaises(ValueError):
            self.analyze([source])
        overflowing = fixture()
        for row in overflowing["attempts"]:
            row.update(cost_usd=1e308, known_cost_subtotal_usd=1e308)
        self.assert_rejected(overflowing)
        for options in ({"resamples": 0}, {"resamples": 1}, {"resamples": True},
                        {"resamples": 2.5}, {"seed": True}, {"seed": 1.5}):
            with self.subTest(options=options):
                self.assert_rejected(fixture(), **options)

    def test_duplicate_json_keys_and_inconsistent_accounting_are_rejected(self):
        source = self.save(fixture())
        text = source.read_text()
        source.write_text(text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1))
        with self.assertRaises(ValueError):
            self.analyze([source])
        for update in ({"accounting_complete": False}, {"known_cost_subtotal_usd": 900},
                       {"cost_basis": []}, {"success": False}, {"status": "running"}):
            run = fixture()
            run["attempts"][0].update(update)
            self.assert_rejected(run)

    def test_byte_determinism_and_source_order_independence(self):
        sources = [self.save(fixture(run_id="first")), self.save(fixture(run_id="second"))]
        _, first = self.analyze(sources, seed=0)
        _, again = self.analyze(list(reversed(sources)), seed=0)
        for name in OUTPUT_FILES:
            self.assertEqual((first.parent / name).read_bytes(), (again.parent / name).read_bytes(), name)
        run = fixture()
        source = self.save(run)
        before, _ = self.analyze([source], seed=0)
        run["attempts"].reverse()
        source = self.save(run)
        after, _ = self.analyze([source], seed=0)
        self.assertEqual(before["metrics"], after["metrics"])
        self.assertNotEqual(before["sources"][0]["sha256"], after["sources"][0]["sha256"])

    def test_existing_outputs_inputs_and_symlinks_cannot_be_overwritten(self):
        source = self.save(fixture())
        original = source.read_bytes()
        _, path = self.analyze([source])
        saved = {name: (path.parent / name).read_bytes() for name in OUTPUT_FILES}
        with self.assertRaises(ValueError):
            write_comparison([source], path.parent, baseline="baseline", candidate="candidate")
        self.assertEqual(saved, {name: (path.parent / name).read_bytes() for name in OUTPUT_FILES})
        other = self.directory / "symlink-output"
        other.mkdir()
        (other / "COMPARISON.json").symlink_to(source)
        with self.assertRaises(ValueError):
            write_comparison([source], other, baseline="baseline", candidate="candidate")
        self.assertEqual(source.read_bytes(), original)
        input_dir = self.directory / "input-named-like-output"
        input_dir.mkdir()
        input_source = input_dir / "COMPARISON.json"
        input_source.write_bytes(original)
        with self.assertRaises(ValueError):
            write_comparison([input_source], input_dir, baseline="baseline", candidate="candidate")
        self.assertEqual(input_source.read_bytes(), original)

    def test_partial_publication_does_not_remove_a_concurrently_created_artifact(self):
        source = self.save(fixture())
        output = self.directory / "racing-output"
        original_open = Path.open

        def race(path, mode="r", *args, **kwargs):
            if path == output / "TASKS.csv" and mode == "x":
                with original_open(path, "w") as stream:
                    stream.write("concurrent-user-file")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", race), self.assertRaises(FileExistsError):
            write_comparison([source], output, baseline="baseline", candidate="candidate")
        self.assertFalse((output / "COMPARISON.json").exists())
        self.assertEqual((output / "TASKS.csv").read_text(), "concurrent-user-file")

    def reviewed(self):
        source = self.save(fixture())
        run = json.loads(source.read_text())
        run["accounting_review"] = {
            "original_run": "run.json", "source": "audit.json",
            "original_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        run["attempts"][0].update(cost_usd=2, known_cost_subtotal_usd=2)
        (source.parent / "audit.json").write_text('{"kind":"synthetic analytical accounting review"}')
        path = source.parent / "run-accounting-reviewed.json"
        path.write_text(json.dumps(run))
        return source, path, run

    def test_explicit_reviewed_accounting_checks_original_audit_and_unchanged_outcomes(self):
        original, reviewed, run = self.reviewed()
        data, _ = self.analyze([reviewed])
        provenance = data["sources"][0]
        self.assertEqual(provenance["path"], str(reviewed))
        self.assertEqual(provenance["accounting_review"]["original_path"], str(original))
        self.assertEqual(data["aggregates"]["baseline"]["total_cost_usd"], 11)
        # Supplying the original source is an explicit choice, not an auto-upgrade.
        unreviewed, _ = self.analyze([original])
        self.assertEqual(unreviewed["aggregates"]["baseline"]["total_cost_usd"], 10)
        with self.assertRaises(ValueError):
            self.analyze([original, reviewed])
        for mutate in (
            lambda r: r["attempts"][0].update(wall_seconds=999),
            lambda r: r["accounting_review"].update(original_sha256="incorrect"),
            lambda r: r["accounting_review"].update(source="../outside.json"),
        ):
            changed = copy.deepcopy(run)
            mutate(changed)
            reviewed.write_text(json.dumps(changed))
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.analyze([reviewed])

    def test_source_mutation_during_analysis_aborts_publication(self):
        source = self.save(fixture())
        import bedrock_bench.research_compare as module
        original_build = module._build

        def changing_source(*args, **kwargs):
            data = original_build(*args, **kwargs)
            source.write_text(source.read_text() + " ")
            return data

        with patch.object(module, "_build", changing_source), self.assertRaises(ValueError):
            self.analyze([source])
        self.assertFalse((self.directory / "comparison-1").exists())

    def test_standalone_plugin_api_works_without_site_packages_repo_or_execution(self):
        run = upstream_fixture()
        missing_upstream_version(next(row for row in run["attempts"] if row["status"] == "timeout"))
        source = self.save(run)
        installed = self.directory / "standalone-plugin"
        shutil.copytree(PLUGIN / "scripts", installed / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(SKILL, installed / "skills/compare-experiments",
                        ignore=shutil.ignore_patterns(".test-research-*", "__pycache__"))
        output = self.directory / "standalone-output"
        code = """
import json, pathlib, socket, subprocess, sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError("Saved comparison must not use network, inference, or subprocess execution")
socket.socket = forbidden
subprocess.Popen = forbidden
from bedrock_bench.research_compare import write_comparison
path = write_comparison([sys.argv[2]], sys.argv[3], baseline="baseline", candidate="candidate")
assert path.is_file()
data = json.loads(path.with_suffix(".json").read_text())
print(json.dumps({"delta": data["metrics"]["success_rate"]["delta"], "tasks": data["pairing"]["distinct_tasks"],
                  "missing_versions": data["selections"]["candidate"]["runner_version_evidence"]["missing_attempts"]}))
"""
        process = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                                  str(installed / "scripts"), str(source), str(output)],
                                 cwd=installed, text=True, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertAlmostEqual(result["delta"], 1 / 6)
        self.assertEqual(result["tasks"], 3)
        self.assertEqual(result["missing_versions"], 1)

    def test_current_engine_saved_demo_schema_is_accepted_without_inference(self):
        from bedrock_bench.config import Experiment, Target
        from bedrock_bench.engine import execute
        from bedrock_bench.tasks import TASK_IDS

        experiment = Experiment(
            name="Synthetic engine schema integration",
            targets=[Target("baseline", "demo", "synthetic", "fixture-imperfect"),
                     Target("candidate", "demo", "synthetic", "fixture-perfect")],
            tasks=list(TASK_IDS), repetitions=2,
        )
        run_dir, run = execute(experiment, self.directory / "engine-demo")
        self.assertTrue(run["synthetic"])
        original = (run_dir / "run.json").read_bytes()
        data, _ = self.analyze([run_dir / "run.json"])
        self.assertEqual(data["aggregates"]["baseline"]["successes"], 4)
        self.assertEqual(data["aggregates"]["candidate"]["successes"], 6)
        self.assertAlmostEqual(data["metrics"]["success_rate"]["delta"], 1 / 3)
        self.assertAlmostEqual(data["metrics"]["cost_per_success_usd"]["delta"], -.004)
        self.assertIsNone(data["protocol"])
        self.assertIsNone(data["metrics"]["mean_agent_seconds"]["delta"])
        self.assertEqual((run_dir / "run.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
