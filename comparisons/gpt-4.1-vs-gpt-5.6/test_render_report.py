"""Offline publication regressions: measurements, reader interpretation, and integrity.

Run directly with Python 3.10+; all mutation tests operate on temporary copies.
The tests do not update the publication manifest, invoke inference, or write
bytecode into this inventory-verified reporting package.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parent
SCORECARD_PATH = "results/2026-08-30/scorecard.json"
CSV_PATH = "results/2026-08-30/scorecard.csv"
MANIFEST_PATH = "evidence/publication.json"
# RC2 evidence: a reporting-only revision must preserve both artifacts exactly.
SCORECARD_SHA256 = "1fc8900b08ef308b1f5ed1e1a36df28685d7395a1f61fe07a196a425c811aa37"
CSV_SHA256 = "f2e769c69c5e9b37ed436e970764f07e1c64c22f11dba9c4276a677e1fdb447e"
DIRECT = "gpt-4.1-2025-04-14"
MODELS = [DIRECT, "openai.gpt-5.6-luna", "openai.gpt-5.6-terra", "openai.gpt-5.6-sol"]
MODEL_LABELS = {DIRECT: "GPT-4.1", **{m: m.rsplit("-", 1)[-1].title() for m in MODELS[1:]}}
BENCHMARK_LABELS = {
    "classification-routing": "Classification routing",
    "structured-document-extraction": "Synthetic invoice extraction",
    "banking77": "Banking77",
    "cord-ocr": "CORD OCR",
    "cord-image": "CORD image",
    "extractbench": "ExtractBench",
    "gsm8k": "GSM8K",
    "math500": "MATH-500",
    "aime": "AIME",
    "gpqa": "GPQA",
    "mmlu_pro": "MMLU-Pro",
    "humaneval": "HumanEval",
}


def load_renderer(package):
    """Load an isolated renderer without import-time .pyc files."""
    path = package / "render_report.py"
    namespace = {"__file__": str(path), "__name__": "publication_renderer_test"}
    exec(compile(path.read_bytes(), str(path), "exec"), namespace)
    return namespace


def markdown_tables(markdown):
    """Read simple report tables, preserving their enclosing heading."""
    tables = []
    lines = markdown.splitlines()
    heading = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#"):
            heading = line.lstrip("# ").strip()
        if line.startswith("|") and index + 1 < len(lines):
            header = [cell.strip() for cell in line.strip("|").split("|")]
            separator = [cell.strip() for cell in lines[index + 1].strip("|").split("|")]
            if len(header) == len(separator) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
                index += 2
                rows = []
                while index < len(lines) and lines[index].startswith("|"):
                    values = [cell.strip() for cell in lines[index].strip("|").split("|")]
                    if len(values) != len(header):
                        raise AssertionError(f"Malformed table under {heading}: {lines[index]}")
                    rows.append(dict(zip(header, values)))
                    index += 1
                tables.append({"heading": heading, "header": header, "rows": rows})
                continue
        index += 1
    return tables


def numeric_values(value):
    """Extract displayed numbers while accepting ordinary currency/interval punctuation."""
    return [float(number) for number in re.findall(r"[+-]?\d+(?:\.\d+)?", value.replace("−", "-").replace(",", ""))]


class ReportRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = load_renderer(PACKAGE)
        cls.source_bytes = (PACKAGE / SCORECARD_PATH).read_bytes()
        cls.scorecard = json.loads(cls.source_bytes)
        cls.cells = {(c["benchmark"], c["model_id"], c["effort"]): c for c in cls.scorecard["cells"]}
        cls.generated = cls.renderer["make_report"]()
        cls.report = cls.generated["RESULTS.md"]
        cls.tables = markdown_tables(cls.report)

    def table(self, *column_fragments, heading=None):
        matches = [table for table in self.tables
                   if (heading is None or heading.casefold() in table["heading"].casefold())
                   and all(any(fragment.casefold() in column.casefold() for column in table["header"])
                           for fragment in column_fragments)]
        self.assertEqual(len(matches), 1, f"Expected one table for {column_fragments}, heading={heading}; found {len(matches)}")
        return matches[0]

    def column(self, table, fragment):
        matches = [column for column in table["header"] if fragment.casefold() in column.casefold()]
        self.assertEqual(len(matches), 1, f"Ambiguous column {fragment!r}: {table['header']}")
        return matches[0]

    def assert_numbers(self, text, expected, places=2):
        values = numeric_values(text)
        self.assertEqual(len(values), len(expected), text)
        for value, wanted in zip(values, expected):
            self.assertAlmostEqual(value, wanted, places=places)

    def clone(self, temporary_root):
        return Path(shutil.copytree(PACKAGE, Path(temporary_root) / "publication"))

    def cli(self, package, mode="--verify"):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, "-B", str(package / "render_report.py"), mode],
                              cwd=package, env=env, text=True, capture_output=True, timeout=30)

    def test_original_measurements_and_csv_are_byte_preserved(self):
        self.assertEqual(hashlib.sha256(self.source_bytes).hexdigest(), SCORECARD_SHA256)
        self.assertEqual(hashlib.sha256((PACKAGE / CSV_PATH).read_bytes()).hexdigest(), CSV_SHA256)
        self.assertEqual(hashlib.sha256(self.generated[CSV_PATH].encode()).hexdigest(), CSV_SHA256)
        self.assertEqual(len(self.cells), 120)
        rows = list(csv.DictReader(io.StringIO(self.generated[CSV_PATH])))
        self.assertEqual(len(rows), 120)
        self.assertEqual({(row["benchmark"], row["model_id"], row["effort"]) for row in rows}, set(self.cells))
        self.assertEqual((PACKAGE / SCORECARD_PATH).read_bytes(), self.source_bytes)

    def test_main_quality_tables_pair_both_invoice_rules_with_their_own_cost(self):
        for effort in ("none", "low", "high"):
            table = self.table("Benchmark", "Metric", "Sol $/correct", heading=f"reasoning: {effort}")
            invoices = [row for row in table["rows"] if row["Benchmark"] == "Synthetic invoice extraction"]
            self.assertEqual(len(invoices), 2)
            strict = next(row for row in invoices if "strict" in row["Metric"].casefold())
            normalized = next(row for row in invoices if "normalized" in row["Metric"].casefold())
            self.assertIn("post hoc", normalized["Metric"].casefold())
            self.assertIn("exploratory", normalized["Metric"].casefold())
            for model in MODELS:
                label = MODEL_LABELS[model]
                cell = self.cells[("structured-document-extraction", model, "omitted" if model == DIRECT else effort)]
                for row, metric in ((strict, "secondary"), (normalized, "primary")):
                    with self.subTest(effort=effort, model=model, metric=metric):
                        self.assertEqual(row["N"], str(cell["planned"]))
                        self.assertEqual(row[label], f"{100 * cell[metric]:.2f}%")
                        cost = cell["operational_unchanged"]["cost_bounds_usd"]["metered_lower_bound_usd"] / cell[f"{metric}_successes"]
                        self.assertEqual(row[f"{label} $/correct"], f"${cost:.6f}")
            if effort == "high":
                self.assertEqual(strict["Sol"], "84.38%")
                self.assertEqual(normalized["Sol"], "98.96%")

    def test_f1_metrics_never_claim_discrete_correct_counts_or_cost_per_correct(self):
        rows = list(csv.DictReader(io.StringIO(self.generated[CSV_PATH])))
        f1_rows = [row for row in rows if row["benchmark"] in {"cord-ocr", "cord-image", "extractbench"}]
        self.assertEqual(len(f1_rows), 30)
        for row in f1_rows:
            with self.subTest(model=row["model_id"], effort=row["effort"], benchmark=row["benchmark"]):
                self.assertEqual(row["primary_cost_per_success_usd"], "")
                self.assertEqual(row["secondary_cost_per_success_usd"], "")
                cell = self.cells[(row["benchmark"], row["model_id"], row["effort"])]
                self.assertIsNone(self.renderer["success_count"](cell))
                self.assertEqual(self.renderer["format_cost_per_correct"](cell), "N/A")
        for effort in ("none", "low", "high"):
            table = self.table("Benchmark", "Metric", "Sol $/correct", heading=f"reasoning: {effort}")
            for row in table["rows"]:
                if "F1" in row["Metric"]:
                    for model in MODELS:
                        self.assertEqual(row[f"{MODEL_LABELS[model]} $/correct"], "N/A")

    def test_all_saved_paired_comparisons_show_uncertainty_and_adjusted_test_decisions(self):
        coverage = self.table("Saved per-configuration", "Saved paired comparisons")
        self.assertEqual(len(coverage["rows"]), 12)
        covered = {"Synthetic invoice extraction", "MATH-500", "AIME"}
        for row in coverage["rows"]:
            if row["Benchmark"] in covered:
                self.assertNotIn("unavailable", row["Saved paired comparisons"].casefold())
            else:
                self.assertIn("unavailable", row["Saved paired comparisons"].casefold())
                self.assertIn("unavailable", row["Saved per-configuration 95% intervals"].casefold())
        observed_total = 0
        for family, heading in (("gpt41-vs-gpt56-none", "GPT-4.1 versus GPT-5.6 none"),
                                ("gpt56-low-vs-high", "GPT-5.6 low versus high")):
            table = self.table("Paired cases", "Delta", "Holm-adjusted p", heading=heading)
            interval_column = self.column(table, "Paired 95% interval")
            self.assertIn("unadjusted", interval_column.casefold())
            expected = []
            for item in self.scorecard["paired_comparisons"]:
                if item["comparison"] == family:
                    expected.extend((item, metric, item[metric], "holm_adjusted_p_within_family")
                                    for metric in ("strict", "normalized"))
            for item in self.scorecard["unchanged_paired_comparisons_from_v1_0"]:
                if item["comparison"] == family:
                    expected.append((item, "corrected", item["corrected"], "holm_adjusted_p_within_workload"))
            self.assertEqual(len(table["rows"]), len(expected))
            observed_total += len(table["rows"])
            for item, metric, stats, p_key in expected:
                matches = [row for row in table["rows"]
                           if row["Benchmark"] == BENCHMARK_LABELS[item["benchmark"]]
                           and row["Model"] == MODEL_LABELS[item["candidate_model"]]
                           and metric in row["Scoring rule"].casefold()]
                self.assertEqual(len(matches), 1, (family, item["benchmark"], item["candidate_model"], metric))
                row = matches[0]
                self.assertEqual(int(row["Paired cases"]), stats["paired_tasks"])
                self.assert_numbers(row["Delta (pp)"], [round(100 * stats["delta"], 2)])
                self.assert_numbers(row[interval_column], [round(100 * x, 2) for x in stats["paired_95_interval"]])
                self.assertAlmostEqual(float(row["Holm-adjusted p"]), stats[p_key], delta=0.0000005)
                if stats["delta"] == 0:
                    self.assertIn("equality not established", row["Interpretation"].casefold())
                elif stats[p_key] >= 0.05:
                    self.assertEqual(row["Interpretation"], "Difference not established")
                else:
                    self.assertIn("Higher" if stats["delta"] > 0 else "Lower", row["Interpretation"])
                if metric == "normalized":
                    self.assertIn("post hoc", row["Scoring rule"].casefold())
                    self.assertIn("exploratory", row["Scoring rule"].casefold())
                if family == "gpt41-vs-gpt56-none" and item["candidate_model"] == "openai.gpt-5.6-sol" and metric == "normalized":
                    self.assertEqual(row["Delta (pp)"], "+5.21")
                    self.assertAlmostEqual(float(row["Holm-adjusted p"]), 0.063812256, delta=0.0000005)
                    self.assertEqual(row["Interpretation"], "Difference not established")
        self.assertEqual(observed_total, 24)

    def test_wilson_intervals_use_saved_current_scoring_rules_and_full_denominators(self):
        table = self.table("Correct / planned", "Wilson 95% interval")
        interval_column = self.column(table, "Wilson 95% interval")
        self.assertIn("unadjusted", interval_column.casefold())
        expected = []
        for cell in self.scorecard["cells"]:
            if cell["benchmark"] == "structured-document-extraction":
                expected.extend((cell, metric, cell[f"{metric}_wilson_95"])
                                for metric in ("primary", "secondary"))
            elif cell["benchmark"] in {"math500", "aime"}:
                expected.append((cell, "primary", cell["source_v1_0"]["corrected_wilson_95"]))
        self.assertEqual(len(table["rows"]), 40)
        self.assertEqual(len(expected), 40)
        self.assertEqual(len({(row["Benchmark"], row["Model"], row["Reasoning"]) for row in table["rows"]}), 30)
        for cell, metric, interval in expected:
            rule = "strict" if metric == "secondary" else "normalized" if cell["benchmark"] == "structured-document-extraction" else "corrected"
            rows = [row for row in table["rows"]
                    if row["Benchmark"] == BENCHMARK_LABELS[cell["benchmark"]]
                    and row["Model"] == MODEL_LABELS[cell["model_id"]]
                    and row["Reasoning"] == cell["effort"]
                    and rule in row["Scoring rule"].casefold()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["Correct / planned"], f"{round(cell[metric] * cell['planned'])}/{cell['planned']}")
            self.assertEqual(row["Accuracy"], f"{100 * cell[metric]:.2f}%")
            self.assert_numbers(row[interval_column], [round(100 * x, 2) for x in interval])

    def test_every_completion_gap_is_visible_with_attempt_reasons_kept_distinct(self):
        table = self.table("Workload completion", "Overall completion", "Uncompleted cases", "Recorded attempt events, including retries")
        expected = [cell for cell in self.scorecard["cells"]
                    if cell["planned"] > cell["operational_unchanged"]["api_completed"]]
        self.assertEqual(len(table["rows"]), len(expected))
        self.assertEqual(len(expected), 13)
        self.assertIn("Recorded attempt events count", self.report)
        self.assertIn("need not sum to the case gap", self.report)
        for cell in expected:
            rows = [row for row in table["rows"]
                    if row["Benchmark"] == BENCHMARK_LABELS[cell["benchmark"]]
                    and row["Model"] == MODEL_LABELS[cell["model_id"]]
                    and row["Reasoning"] == cell["effort"]]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            op = cell["operational_unchanged"]
            self.assertEqual(row["Completed / planned"], f"{op['api_completed']}/{cell['planned']}")
            self.assertEqual(int(row["Uncompleted cases"]), cell["planned"] - op["api_completed"])
            self.assertEqual(row["Workload completion"], f"{100 * op['api_completed'] / cell['planned']:.2f}%")
            condition = [c for c in self.scorecard["cells"] if (c["model_id"], c["effort"]) == (cell["model_id"], cell["effort"])]
            overall = 100 * sum(c["operational_unchanged"]["api_completed"] for c in condition) / sum(c["planned"] for c in condition)
            self.assertEqual(row["Overall completion"], f"{overall:.2f}%")
            if not any(op["error_families_by_attempt"].values()):
                self.assertIn("not classified", row["Recorded attempt events, including retries"])
            if cell["benchmark"] == "extractbench" and cell["effort"] == "high" and row["Model"] in {"Sol", "Luna"}:
                self.assertIn("File-input limit: 2", row["Recorded attempt events, including retries"])
                expected_output_events = 36 if row["Model"] == "Sol" else 42
                self.assertIn(f"Output-token limit: {expected_output_events}", row["Recorded attempt events, including retries"])
                if row["Model"] == "Sol":
                    self.assertEqual(row["Completed / planned"], "332/370")
                    self.assertIn("Missing usage metadata: 4", row["Recorded attempt events, including retries"])
                    self.assertEqual(int(row["Uncompleted cases"]), 38)
                else:
                    self.assertEqual(row["Completed / planned"], "326/370")
                    self.assertEqual(int(row["Uncompleted cases"]), 44)

    def test_missing_usage_keeps_both_cost_bounds_visible_at_workload_level(self):
        table = self.table("Benchmark", "Completed / planned", "TTFT p50", heading="Sol / high")
        row = next(row for row in table["rows"] if row["Benchmark"] == "ExtractBench")
        self.assertIn("$78.10–$115.46", " | ".join(row.values()))
        usage_column = self.column(table, "missing usage")
        self.assertEqual(int(row[usage_column]), 4)
        self.assertIn("lower bound", self.report.casefold())
        self.assertIn("conservative upper", self.report.casefold())
        self.assertIn("confidence interval", self.report.casefold())
        self.assertIn("final attempt", self.report.casefold())
        self.assertIn("backoff", self.report.casefold())

    def test_cli_verifies_the_complete_published_inventory(self):
        result = self.cli(PACKAGE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_write_recreates_removed_outputs_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            package = self.clone(temporary_root)
            for name in ("RESULTS.md", CSV_PATH):
                (package / name).unlink()
            result = self.cli(package, "--write")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, expected in self.generated.items():
                self.assertEqual((package / name).read_bytes(), expected.encode())
            self.assertEqual((package / SCORECARD_PATH).read_bytes(), self.source_bytes)
            self.assertEqual((package / MANIFEST_PATH).read_bytes(), (PACKAGE / MANIFEST_PATH).read_bytes())

    def test_integrity_rejects_source_drift_unsafe_paths_and_incomplete_matrix(self):
        for failure in ("source-drift", "path-traversal", "symlink", "missing-cell"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary_root:
                package = self.clone(temporary_root)
                source_path = package / SCORECARD_PATH
                manifest_path = package / MANIFEST_PATH
                manifest = json.loads(manifest_path.read_bytes())
                expected_error = ""
                if failure == "source-drift":
                    source_path.write_bytes(source_path.read_bytes() + b"\n")
                    expected_error = "release file drift"
                elif failure == "path-traversal":
                    manifest["files"]["../outside.json"] = {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
                    manifest_path.write_text(json.dumps(manifest))
                    expected_error = "invalid package-relative manifest path"
                elif failure == "symlink":
                    outside = Path(temporary_root) / "outside.json"
                    outside.write_bytes(source_path.read_bytes())
                    source_path.unlink()
                    source_path.symlink_to(outside)
                    expected_error = "manifest path escapes package"
                else:
                    source = json.loads(source_path.read_bytes())
                    source["cells"].pop()
                    source_path.write_text(json.dumps(source))
                    content = source_path.read_bytes()
                    manifest["files"][SCORECARD_PATH].update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
                    manifest_path.write_text(json.dumps(manifest))
                    expected_error = "full matrix coverage mismatch"
                result = self.cli(package)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(expected_error, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
