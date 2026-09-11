"""Check shipped chart data against saved evidence using only the standard library.

Run directly with ``python3 -B test_render_charts.py``. These checks read the
published JSON/HTML/SVG artifacts; they do not need the plotting dependency,
regenerate charts, update manifests, make network requests, or write files.
"""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree


PACKAGE = Path(__file__).resolve().parent
SOURCE = PACKAGE / "results/2026-08-30/scorecard.json"
RUN_SETTINGS = PACKAGE / "evidence/run-settings.json"
CHART_DATA = PACKAGE / "charts/chart-data.json"
HTML = PACKAGE / "CHARTS.html"


class EmbeddedDataParser(HTMLParser):
    """Read embedded JSON and resource references without executing JavaScript."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.resources = []
        self.active_script = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script":
            self.active_script = {"attributes": attrs, "text": ""}
            self.scripts.append(self.active_script)
        if tag in {"script", "img", "iframe", "source", "video", "audio"} and attrs.get("src"):
            self.resources.append(attrs["src"])
        if tag == "link" and attrs.get("rel", "").lower() == "stylesheet":
            self.resources.append(attrs.get("href", ""))

    def handle_data(self, data):
        if self.active_script is not None:
            self.active_script["text"] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.active_script = None

    def json_block(self, block_id):
        matches = [script for script in self.scripts
                   if script["attributes"].get("id") == block_id]
        if len(matches) != 1:
            raise AssertionError(f"Expected one embedded JSON block {block_id!r}; found {len(matches)}")
        if matches[0]["attributes"].get("type") != "application/json":
            raise AssertionError(f"Embedded block {block_id!r} must use application/json")
        return json.loads(matches[0]["text"])


def cell_key(cell):
    return cell["benchmark"], cell["model_id"], cell["effort"]


class ChartEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_bytes = SOURCE.read_bytes()
        cls.run_settings_bytes = RUN_SETTINGS.read_bytes()
        cls.source = json.loads(cls.source_bytes)
        cls.settings = json.loads(cls.run_settings_bytes)
        cls.data = json.loads(CHART_DATA.read_bytes())
        cls.saved = {cell_key(cell): cell for cell in cls.source["cells"]}
        cls.runs = {(record["workload"], record["model_id"], record["reasoning_effort"]): record
                    for record in cls.settings["records"]}
        cls.chart_cells = {cell_key(cell): cell for cell in cls.data["cells"]}
        cls.parser = EmbeddedDataParser()
        cls.parser.feed(HTML.read_text())
        cls.parser.close()

    def test_all_120_chart_cells_match_saved_values_and_units(self):
        self.assertEqual(self.data["source_sha256"], hashlib.sha256(self.source_bytes).hexdigest())
        self.assertEqual(self.data["run_settings_sha256"], hashlib.sha256(self.run_settings_bytes).hexdigest())
        self.assertEqual(len(self.data["cells"]), 120)
        self.assertEqual(len(self.chart_cells), 120, "Duplicate chart cells must not replace a configuration")
        self.assertEqual(set(self.chart_cells), set(self.saved))
        self.assertEqual(set(self.chart_cells), set(self.runs))
        for key, chart in self.chart_cells.items():
            with self.subTest(cell=key):
                source = self.saved[key]
                operational = source["operational_unchanged"]
                run = self.runs[key]
                cost = operational["cost_bounds_usd"]
                expected = {
                    "benchmark": source["benchmark"],
                    "model_id": source["model_id"],
                    "effort": source["effort"],
                    "planned": source["planned"],
                    "completed": operational["api_completed"],
                    "completion_percent": 100 * operational["api_completed"] / source["planned"],
                    "ttft_samples": run["ttft_latency_observations"],
                    "e2e_samples": run["e2e_latency_observations"],
                    "cost_lower_usd": cost["metered_lower_bound_usd"],
                    "cost_upper_usd": cost["conservative_upper_bound_usd"],
                    "missing_usage_attempts": cost["unpriced_usage_missing_attempts"],
                    "primary": source["primary"],
                    "primary_metric": source["primary_metric"],
                    "secondary": source["secondary"],
                    "secondary_metric": source["secondary_metric"],
                }
                for source_metric, chart_metric in (("ttft_ms", "ttft"), ("end_to_end_ms", "e2e")):
                    for percentile in ("p50", "p95"):
                        value = operational.get(source_metric, {}).get(percentile)
                        expected[f"{chart_metric}_{percentile}_s"] = value / 1000 if value is not None else None
                for field, value in expected.items():
                    self.assertIn(field, chart)
                    if isinstance(value, float):
                        self.assertAlmostEqual(chart[field], value, delta=1e-12, msg=f"{key}: {field}")
                    else:
                        self.assertEqual(chart[field], value, f"{key}: {field}")

    def test_latency_sample_counts_are_not_replaced_by_completed_request_counts(self):
        mismatch_keys = {key for key, saved in self.saved.items()
                         if self.runs[key]["ttft_latency_observations"] != saved["operational_unchanged"]["api_completed"]}
        self.assertTrue(mismatch_keys, "This evidence must exercise the TTFT population distinction")
        for key in mismatch_keys:
            with self.subTest(cell=key):
                chart = self.chart_cells[key]
                self.assertEqual(chart["ttft_samples"], self.runs[key]["ttft_latency_observations"])
                self.assertNotEqual(chart["ttft_samples"], chart["completed"])
        luna_high = self.chart_cells[("extractbench", "openai.gpt-5.6-luna", "high")]
        self.assertEqual((luna_high["completed"], luna_high["ttft_samples"], luna_high["e2e_samples"]), (326, 365, 370))
        sol_high = self.chart_cells[("extractbench", "openai.gpt-5.6-sol", "high")]
        self.assertEqual((sol_high["completed"], sol_high["ttft_samples"], sol_high["e2e_samples"]), (332, 367, 370))

    def test_missing_usage_preserves_sol_high_extractbench_cost_uncertainty(self):
        chart = self.chart_cells[("extractbench", "openai.gpt-5.6-sol", "high")]
        self.assertEqual(chart["missing_usage_attempts"], 4)
        self.assertAlmostEqual(chart["cost_lower_usd"], 78.10004906, places=8)
        self.assertAlmostEqual(chart["cost_upper_usd"], 115.46273706, places=8)
        self.assertGreater(chart["cost_upper_usd"], chart["cost_lower_usd"])
        self.assertEqual((chart["completed"], chart["planned"]), (332, 370))
        self.assertAlmostEqual(chart["completion_percent"], 100 * 332 / 370, places=10)

    def test_html_embeds_the_same_data_and_all_36_workload_effort_figures(self):
        self.assertEqual(self.parser.json_block("chart-data"), self.data)
        figures = self.parser.json_block("figures")
        benchmarks = {cell["benchmark"] for cell in self.source["cells"]}
        expected_keys = {f"{benchmark}|{effort}" for benchmark in benchmarks for effort in ("none", "low", "high")}
        self.assertEqual(len(benchmarks), 12)
        self.assertEqual(len(figures), 36)
        self.assertEqual(set(figures), expected_keys)
        for key, svg_text in figures.items():
            with self.subTest(figure=key):
                self.assertIsInstance(svg_text, str)
                svg = ElementTree.fromstring(svg_text)
                self.assertEqual(svg.tag.rsplit("}", 1)[-1], "svg")
                self.assertTrue(any(element.tag.rsplit("}", 1)[-1] in {"path", "rect", "line", "text"}
                                    for element in svg.iter()), "SVG figure has no drawing content")
                for element in svg.iter():
                    self.assertNotEqual(element.tag.rsplit("}", 1)[-1], "script", "SVG must not contain executable scripts")
                    for attribute, value in element.attrib.items():
                        if attribute.rsplit("}", 1)[-1] == "href":
                            self.assertTrue(value.startswith(("#", "data:")), f"Non-embedded SVG resource: {value}")

    def test_every_svg_displays_the_selected_models_source_values_and_sample_counts(self):
        model_ids = ("gpt-4.1-2025-04-14", "openai.gpt-5.6-luna", "openai.gpt-5.6-terra", "openai.gpt-5.6-sol")
        model_labels = ["GPT-4.1", "Luna", "Terra", "Sol"]
        titles = {
            "classification-routing": "Classification routing",
            "structured-document-extraction": "Synthetic invoice extraction",
            "banking77": "Banking77", "cord-ocr": "CORD OCR", "cord-image": "CORD image",
            "extractbench": "ExtractBench", "gsm8k": "GSM8K", "math500": "MATH-500",
            "aime": "AIME", "gpqa": "GPQA Diamond", "mmlu_pro": "MMLU-Pro", "humaneval": "HumanEval",
        }
        def visible_texts(element):
            return [" ".join("".join(node.itertext()).split()) for node in element.iter()
                    if node.tag.rsplit("}", 1)[-1] == "text"]

        for figure_key, svg_text in self.parser.json_block("figures").items():
            with self.subTest(figure=figure_key):
                benchmark, effort = figure_key.split("|")
                svg = ElementTree.fromstring(svg_text)
                texts = visible_texts(svg)
                self.assertIn(titles[benchmark], texts)
                self.assertTrue(any(f"GPT-5.6 reasoning: {effort} | GPT-4.1: omitted" in text for text in texts))
                axes = [node for node in svg.iter() if re.fullmatch(r"(?:qc-)?axes_\d+", node.get("id", ""))]
                self.assertEqual(len(axes), 4, "Each workload figure needs two latency panels, completion, and spend")
                panels = {heading: next((axis for axis in axes if heading in visible_texts(axis)), None)
                          for heading in ("Time to first text (TTFT)", "Response latency (final attempt)", "API completion", "Recorded spend")}
                for heading, axis in panels.items():
                    self.assertIsNotNone(axis, heading)
                    self.assertEqual([text for text in visible_texts(axis) if text in model_labels], model_labels,
                                     "Visible metric rows must follow the labeled model order")
                cells = [self.saved[(benchmark, model, "omitted" if model == model_ids[0] else effort)] for model in model_ids]
                for heading, source_metric, population_field in (
                        ("Time to first text (TTFT)", "ttft_ms", "ttft_latency_observations"),
                        ("Response latency (final attempt)", "end_to_end_ms", "e2e_latency_observations")):
                    panel_texts = visible_texts(panels[heading])
                    labels = [text for text in panel_texts if re.fullmatch(r"(?:N/A|\d+\.\d{3}) / (?:N/A|\d+\.\d{3}) s · n=[\d,]+", text)]
                    expected = []
                    for cell in cells:
                        operational = cell["operational_unchanged"]
                        quantiles = [operational.get(source_metric, {}).get(percentile) for percentile in ("p50", "p95")]
                        formatted = [f"{value / 1000:.3f}" if value is not None else "N/A" for value in quantiles]
                        population = self.runs[cell_key(cell)][population_field]
                        expected.append(f"{formatted[0]} / {formatted[1]} s · n={population:,}")
                    self.assertEqual(labels, expected, f"{figure_key}: {heading} labels differ from source")
                completion_labels = [text for text in visible_texts(panels["API completion"])
                                     if re.fullmatch(r"[\d,]+/[\d,]+ · \d+\.\d{2}% · gap [\d,]+", text)]
                expected_completion = []
                expected_spend = []
                for cell in cells:
                    operational = cell["operational_unchanged"]
                    completed, planned = operational["api_completed"], cell["planned"]
                    expected_completion.append(f"{completed:,}/{planned:,} · {100 * completed / planned:.2f}% · gap {planned - completed:,}")
                    cost = operational["cost_bounds_usd"]
                    lower, upper = cost["metered_lower_bound_usd"], cost["conservative_upper_bound_usd"]
                    lower_text = f"${lower:.4f}" if lower < 1 else f"${lower:.2f}"
                    upper_text = f"${upper:.4f}" if upper < 1 else f"${upper:.2f}"
                    expected_spend.append(lower_text + (f"–{upper_text} · {cost['unpriced_usage_missing_attempts']} attempts missing usage" if upper > lower else ""))
                self.assertEqual(completion_labels, expected_completion)
                spend_labels = [text for text in visible_texts(panels["Recorded spend"]) if text.startswith("$")]
                self.assertEqual(spend_labels, expected_spend, "Currency symbols and ranges must survive SVG rendering")
                if figure_key == "extractbench|high":
                    self.assertEqual(spend_labels[-1], "$78.10–$115.46 · 4 attempts missing usage")

    def test_quality_cost_figures_display_and_position_the_selected_source_metrics(self):
        figures = self.parser.json_block("quality-cost-figures")
        benchmarks = {cell["benchmark"] for cell in self.source["cells"]}
        self.assertEqual(set(figures), {f"{benchmark}|{effort}" for benchmark in benchmarks for effort in ("none", "low", "high")})
        self.assertEqual(len(figures), 36)
        model_ids = ("gpt-4.1-2025-04-14", "openai.gpt-5.6-luna", "openai.gpt-5.6-terra", "openai.gpt-5.6-sol")
        model_labels = ("GPT-4.1", "Luna", "Terra", "Sol")

        def visible_texts(element):
            return [" ".join("".join(node.itertext()).split()) for node in element.iter()
                    if node.tag.rsplit("}", 1)[-1] == "text"]

        def coordinate_for(axis, dimension, value):
            ticks = [node for node in axis.iter() if re.fullmatch(f"(?:qc-)?{dimension}tick_" + r"\d+", node.get("id", ""))]
            samples = []
            for tick in ticks:
                texts = visible_texts(tick)
                if len(texts) != 1:
                    continue
                try:
                    tick_value = float(texts[0].replace("−", "-").replace(",", "").replace("$", ""))
                except ValueError:
                    continue
                locations = [node for node in tick.iter() if node.tag.rsplit("}", 1)[-1] == "use" and dimension in node.attrib]
                if locations:
                    samples.append((tick_value, float(locations[0].attrib[dimension])))
            self.assertGreaterEqual(len(samples), 2, "At least two visible numeric ticks are needed to check plotted coordinates")
            samples.sort()
            low, high = samples[0], samples[-1]
            self.assertNotEqual(low[0], high[0])
            scale = (high[1] - low[1]) / (high[0] - low[0])
            return low[1] + (value - low[0]) * scale

        for figure_key, svg_text in figures.items():
            with self.subTest(figure=figure_key):
                benchmark, effort = figure_key.split("|")
                svg = ElementTree.fromstring(svg_text)
                self.assertEqual(svg.tag.rsplit("}", 1)[-1], "svg")
                axes = [node for node in svg.iter() if re.fullmatch(r"(?:qc-)?axes_\d+", node.get("id", ""))]
                metrics = (("strict", "secondary"), ("normalized", "primary")) if benchmark == "structured-document-extraction" else (("", "primary"),)
                self.assertEqual(len(axes), len(metrics), "Invoices need separate strict and normalized quality-cost panels")
                point_ids = {node.get("id") for node in svg.iter() if node.get("id", "").startswith("quality-cost-point-")}
                expected_point_ids = {f"quality-cost-point-{rule + '-' if rule else ''}{index}" for rule, _ in metrics for index in range(4)}
                self.assertEqual(point_ids, expected_point_ids)
                for rule, metric in metrics:
                    first_point = f"quality-cost-point-{rule + '-' if rule else ''}0"
                    matching_axes = [axis for axis in axes if any(node.get("id") == first_point for node in axis.iter())]
                    self.assertEqual(len(matching_axes), 1)
                    axis = matching_axes[0]
                    texts = visible_texts(axis)
                    if rule == "strict":
                        self.assertIn("Strict document accuracy", texts)
                    elif rule == "normalized":
                        self.assertIn("Address-normalized accuracy (post hoc; exploratory)", texts)
                    else:
                        self.assertIn(self.saved[(benchmark, model_ids[0], "omitted")]["primary_metric"], texts)
                    # Detail rows are figure-level text aligned beside their plot.
                    # Use the plot's visible vertical bounds to retain metric attribution
                    # when invoices have both strict and normalized panels.
                    panel_top = coordinate_for(axis, "y", 103)
                    panel_bottom = coordinate_for(axis, "y", -3)
                    texts = [" ".join("".join(node.itertext()).split()) for node in svg.iter()
                             if node.tag.rsplit("}", 1)[-1] == "text" and "y" in node.attrib
                             and panel_top - 1 <= float(node.attrib["y"]) <= panel_bottom + 1]
                    expected_scores, expected_spends, expected_completions, expected_models = [], [], [], []
                    for index, (model, label) in enumerate(zip(model_ids, model_labels)):
                        selected_effort = "omitted" if model == model_ids[0] else effort
                        cell = self.saved[(benchmark, model, selected_effort)]
                        op = cell["operational_unchanged"]
                        costs = op["cost_bounds_usd"]
                        lower, upper = costs["metered_lower_bound_usd"], costs["conservative_upper_bound_usd"]
                        expected_models.append(f"{label} / {selected_effort}")
                        expected_scores.append(f"Score: {100 * cell[metric]:.2f} / 100")
                        lower_text = f"${lower:.4f}" if lower < 1 else f"${lower:.2f}"
                        upper_text = f"${upper:.4f}" if upper < 1 else f"${upper:.2f}"
                        expected_spends.append("Recorded spend: " + lower_text + (f"–{upper_text}" if upper > lower else ""))
                        expected_completions.append(f"Completed: {op['api_completed']:,}/{cell['planned']:,} ({100 * op['api_completed'] / cell['planned']:.2f}%)")
                        point_id = f"quality-cost-point-{rule + '-' if rule else ''}{index}"
                        point = next(node for node in axis.iter() if node.get("id") == point_id)
                        markers = [node for node in point.iter() if node.tag.rsplit("}", 1)[-1] == "use" and "x" in node.attrib and "y" in node.attrib]
                        self.assertEqual(len(markers), 1, "Each model must have one plotted quality-cost point")
                        self.assertAlmostEqual(float(markers[0].attrib["x"]), coordinate_for(axis, "x", lower), delta=0.002,
                                               msg=f"{figure_key}/{label}: plotted cost does not match metered spend")
                        self.assertAlmostEqual(float(markers[0].attrib["y"]), coordinate_for(axis, "y", 100 * cell[metric]), delta=0.002,
                                               msg=f"{figure_key}/{label}: plotted quality uses the wrong metric")
                        bound_id = f"quality-cost-bound-{rule + '-' if rule else ''}{index}"
                        bounds = [node for node in axis.iter() if node.get("id") == bound_id]
                        self.assertEqual(len(bounds), 1 if upper > lower else 0)
                        if upper > lower:
                            paths = [node for node in bounds[0].iter() if node.tag.rsplit("}", 1)[-1] == "path"]
                            self.assertEqual(len(paths), 1)
                            endpoints = [float(number) for number in re.findall(r"-?\d+(?:\.\d+)?", paths[0].attrib["d"])]
                            self.assertEqual(len(endpoints), 4, "Accounting bound must be one horizontal segment")
                            expected_endpoints = [coordinate_for(axis, "x", lower), coordinate_for(axis, "y", 100 * cell[metric]),
                                                  coordinate_for(axis, "x", upper), coordinate_for(axis, "y", 100 * cell[metric])]
                            for observed, expected in zip(endpoints, expected_endpoints):
                                self.assertAlmostEqual(observed, expected, delta=0.002)
                        if costs["unpriced_usage_missing_attempts"]:
                            self.assertTrue(any(f"{costs['unpriced_usage_missing_attempts']} attempts missing usage" in text for text in texts))
                    self.assertEqual([text for text in texts if text in expected_models], expected_models)
                    self.assertEqual([text for text in texts if text.startswith("Score: ")], expected_scores)
                    self.assertEqual([text for text in texts if text.startswith("Recorded spend: ")], expected_spends)
                    self.assertEqual([text for text in texts if text.startswith("Completed: ")], expected_completions)
                    if figure_key == "extractbench|high":
                        self.assertIn("Recorded spend: $78.10–$115.46", texts)
                    if figure_key == "structured-document-extraction|high":
                        self.assertEqual(expected_scores[-1], "Score: 84.38 / 100" if rule == "strict" else "Score: 98.96 / 100")

    def test_dashboard_scripts_and_resources_are_self_contained(self):
        for script in self.parser.scripts:
            self.assertNotIn("src", script["attributes"], "Dashboard scripts must be inline")
        for resource in self.parser.resources:
            self.assertTrue(resource.startswith(("data:", "#")), f"Dashboard needs an external resource: {resource}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
