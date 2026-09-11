"""Create offline customer charts from the preserved aggregate scorecard.

Rendering needs matplotlib==3.10.6. Opening CHARTS.html and running the report
and chart-data tests need neither matplotlib nor network access.
"""
import argparse
import hashlib
import html
import io
import json
from pathlib import Path
import re

PACKAGE = Path(__file__).resolve().parent
SOURCE = PACKAGE / "results/2026-08-30/scorecard.json"
SETTINGS = PACKAGE / "evidence/run-settings.json"
DIRECT = "gpt-4.1-2025-04-14"
MODELS = [DIRECT, "openai.gpt-5.6-luna", "openai.gpt-5.6-terra", "openai.gpt-5.6-sol"]
LABELS = ["GPT-4.1", "Luna", "Terra", "Sol"]
COLORS = ["#586575", "#2479bd", "#16867d", "#8963ac"]
WORKLOADS = [
    ("classification-routing", "Classification routing"),
    ("structured-document-extraction", "Synthetic invoice extraction"),
    ("banking77", "Banking77"), ("cord-ocr", "CORD OCR"),
    ("cord-image", "CORD image"), ("extractbench", "ExtractBench"),
    ("gsm8k", "GSM8K"), ("math500", "MATH-500"),
    ("aime", "AIME"), ("gpqa", "GPQA Diamond"),
    ("mmlu_pro", "MMLU-Pro"), ("humaneval", "HumanEval"),
]


def chart_data():
    """Keep every cell, its timing population and cost bounds; never pool quantiles."""
    source_bytes, settings_bytes = SOURCE.read_bytes(), SETTINGS.read_bytes()
    manifest = json.loads((PACKAGE / "evidence/publication.json").read_text())
    for path, content in ((SOURCE, source_bytes), (SETTINGS, settings_bytes)):
        if hashlib.sha256(content).hexdigest() != manifest["files"][path.relative_to(PACKAGE).as_posix()]["sha256"]:
            raise ValueError(f"Measurement input differs from manifest: {path.name}")
    settings = {(r["workload"], r["model_id"], r["reasoning_effort"]): r
                for r in json.loads(settings_bytes)["records"]}
    result = []
    for cell in json.loads(source_bytes)["cells"]:
        key = cell["benchmark"], cell["model_id"], cell["effort"]
        op, timing = cell["operational_unchanged"], settings[key]
        cost = op["cost_bounds_usd"]
        row = {k: cell[k] for k in ("benchmark", "model_id", "effort", "planned", "primary", "primary_metric", "secondary", "secondary_metric")}
        row.update(completed=op["api_completed"], completion_percent=100 * op["api_completed"] / cell["planned"],
                   ttft_samples=timing["ttft_latency_observations"], e2e_samples=timing["e2e_latency_observations"],
                   cost_lower_usd=cost["metered_lower_bound_usd"], cost_upper_usd=cost["conservative_upper_bound_usd"],
                   missing_usage_attempts=cost["unpriced_usage_missing_attempts"])
        for out_key, source_key in (("ttft", "ttft_ms"), ("e2e", "end_to_end_ms")):
            for p in ("p50", "p95"):
                v = op.get(source_key, {}).get(p)
                row[f"{out_key}_{p}_s"] = v / 1000 if v is not None else None
        result.append(row)
    keys = {(r["benchmark"], r["model_id"], r["effort"]) for r in result}
    expected = {(b, m, e) for b, _ in WORKLOADS for m in MODELS
                for e in (("omitted",) if m == DIRECT else ("none", "low", "high"))}
    if len(result) != 120 or keys != expected or set(settings) != expected:
        raise ValueError("Chart inputs must contain the complete 120-cell matrix")
    return {"source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "run_settings_sha256": hashlib.sha256(settings_bytes).hexdigest(), "cells": result}


def selected(data, benchmark, effort):
    lookup = {(r["benchmark"], r["model_id"], r["effort"]): r for r in data["cells"]}
    return [lookup[(benchmark, m, "omitted" if m == DIRECT else effort)] for m in MODELS]


def seconds(v):
    return "N/A" if v is None else f"{v:.3f}"


def currency(v):
    return f"${v:.4f}" if v < 1 else f"${v:.2f}"


def setup_plotting():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
        "axes.titlesize": 13, "axes.titleweight": "bold", "axes.labelsize": 10,
        "text.color": "#253242", "text.parse_math": False, "axes.labelcolor": "#253242", "xtick.color": "#526170",
        "ytick.color": "#253242", "svg.fonttype": "none", "svg.hashsalt": "benchmark-charts-v1",
        "figure.facecolor": "white", "axes.facecolor": "white"})
    import matplotlib.pyplot as plt
    return plt


def style_axis(ax, labels=LABELS):
    ax.set_yticks(range(len(labels)), labels)
    ax.set_ylim(len(labels) - .5, -.5)
    ax.tick_params(axis="y", length=0, pad=8)
    ax.grid(axis="x", color="#e6eaf0", linewidth=.7)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#cbd3dd")


def workload_figure(plt, data, benchmark, title, effort):
    rows = selected(data, benchmark, effort)
    peers = [r for r in data["cells"] if r["benchmark"] == benchmark]
    fig, axes = plt.subplots(2, 2, figsize=(12.4, 8.4))
    fig.subplots_adjust(left=.105, right=.96, top=.80, bottom=.155, hspace=.7, wspace=.42)
    n = rows[0]["planned"]
    fig.text(.045, .958, title, fontsize=21, weight="bold")
    fig.text(.045, .913, f"GPT-5.6 reasoning: {effort}  |  GPT-4.1: omitted  |  {n:,} evaluated cases per configuration", fontsize=11)
    fig.text(.045, .877, "GPT-4.1: direct OpenAI API    •    GPT-5.6: Amazon Bedrock Mantle    •    August 26–30, 2026", fontsize=10, color="#526170")
    for ax, prefix, heading in ((axes[0, 0], "ttft", "Time to first text (TTFT)"),
                                (axes[0, 1], "e2e", "Response latency (final attempt)")):
        style_axis(ax)
        max_value = max((r[f"{prefix}_p95_s"] or 0) for r in peers)
        ax.set_xlim(0, max(max_value * 1.08, .001))
        ax.set_title(heading, loc="left", pad=24)
        ax.text(0, 1.055, "● p50   ○ p95   ·   seconds", transform=ax.transAxes, fontsize=9, color="#526170")
        for i, row in enumerate(rows):
            lo, hi = row[f"{prefix}_p50_s"], row[f"{prefix}_p95_s"]
            if lo is not None and hi is not None:
                ax.plot([lo, hi], [i, i], color=COLORS[i], linewidth=2, zorder=2)
                ax.scatter([lo], [i], s=40, color=COLORS[i], zorder=3)
                ax.scatter([hi], [i], s=44, facecolors="white", edgecolors=COLORS[i], linewidths=1.6, zorder=3)
            label = f"{seconds(lo)} / {seconds(hi)} s  ·  n={row[prefix + '_samples']:,}"
            ax.text(.98, i + .30, label, transform=ax.get_yaxis_transform(), ha="right", fontsize=8.5)
        ax.set_xlabel("Seconds (axis held constant across reasoning settings)")
    ax = axes[1, 0]
    style_axis(ax)
    ax.set_title("API completion", loc="left", pad=18)
    ax.set_xlim(0, 105)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Completed / planned (%) · separate from answer quality")
    for i, row in enumerate(rows):
        ax.barh(i, 100, color="#edf0f4", height=.45)
        ax.barh(i, row["completion_percent"], color=COLORS[i], height=.45)
        ax.text(.01, i + .28, f"{row['completed']:,}/{row['planned']:,}  ·  {row['completion_percent']:.2f}%  ·  gap {row['planned']-row['completed']:,}",
                transform=ax.get_yaxis_transform(), fontsize=8.5, va="top")
    ax = axes[1, 1]
    style_axis(ax)
    ax.set_title("Recorded spend", loc="left", pad=18)
    ax.set_xlim(0, max(r["cost_upper_usd"] for r in peers) * 1.10)
    ax.set_xlabel(f"USD for {n:,} evaluated cases · includes recorded retries")
    for i, row in enumerate(rows):
        lo, hi = row["cost_lower_usd"], row["cost_upper_usd"]
        ax.barh(i, lo, color=COLORS[i], height=.45)
        if hi > lo:
            ax.barh(i, hi-lo, left=lo, height=.45, color="#f2eaf8", edgecolor=COLORS[i], hatch="////", linewidth=.7)
        label = currency(lo) + (f"–{currency(hi)}  ·  {row['missing_usage_attempts']} attempts missing usage" if hi > lo else "")
        ax.text(.01, i + .28, label, transform=ax.get_yaxis_transform(), fontsize=8.5, va="top")
    fig.text(.045, .094, "TTFT: final attempts with text output. Duration: final attempts, including terminal failures/local rejections; excludes earlier retries and backoff.", fontsize=8.8)
    fig.text(.045, .063, "Cost range: metered lower bound to conservative upper estimate, not a confidence interval. Read with workload quality and methodology.", fontsize=8.8)
    fig.text(.045, .032, "Descriptive cross-platform results. Run timing, some output ceilings and overlapping execution differ for none versus low/high.", fontsize=8.8, color="#526170")
    return fig


def quality_cost_figure(plt, data, benchmark, title, effort):
    """Compare one workload and scoring rule at a time, retaining cost bounds."""
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator
    rows = selected(data, benchmark, effort)
    peers = [r for r in data["cells"] if r["benchmark"] == benchmark]
    invoice = benchmark == "structured-document-extraction"
    metrics = [("secondary", "Strict document accuracy", "strict"),
               ("primary", "Address-normalized accuracy (post hoc; exploratory)", "normalized")] if invoice else [
                   ("primary", rows[0]["primary_metric"], "")]
    fig = plt.figure(figsize=(12.4, 12.4 if invoice else 7.3))
    fig.text(.045, .962, f"Quality versus cost: {title}", fontsize=20, weight="bold")
    fig.text(.045, .921, f"GPT-5.6 reasoning: {effort}  |  GPT-4.1: omitted  |  {rows[0]['planned']:,} evaluated cases per configuration", fontsize=11)
    fig.text(.045, .885, "GPT-4.1: direct OpenAI API    •    GPT-5.6: Amazon Bedrock Mantle    •    August 26–30, 2026", fontsize=10, color="#526170")
    shapes = ["o", "s", "D", "^"]
    height, bottoms = (.28, [.535, .155]) if invoice else (.52, [.25])
    for (metric, metric_title, suffix), bottom in zip(metrics, bottoms):
        ax = fig.add_axes([.085, bottom, .52, height])
        ax.set_title(metric_title, loc="left", fontsize=12, pad=15)
        ax.set_xlim(0, max(r["cost_upper_usd"] for r in peers) * 1.10)
        ax.set_ylim(-3, 103)
        ax.set_yticks([0, 20, 40, 60, 80, 100])
        ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.ticklabel_format(axis="x", style="plain", useOffset=False)
        ax.set_xlabel(f"Recorded spend (USD for {rows[0]['planned']:,} evaluated cases)")
        ax.set_ylabel("Quality score (0–100)")
        ax.grid(color="#e6eaf0", linewidth=.7)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("bottom", "left"):
            ax.spines[side].set_color("#cbd3dd")
        for i, row in enumerate(rows):
            lo, hi, quality = row["cost_lower_usd"], row["cost_upper_usd"], 100 * row[metric]
            if hi > lo:
                bounds = ax.errorbar(lo, quality, xerr=[[0], [hi-lo]], fmt="none", ecolor=COLORS[i], capsize=5, linewidth=1.7)
                bounds.lines[2][0].set_gid("quality-cost-bound" + (f"-{suffix}" if suffix else "") + f"-{i}")
            point = ax.scatter(lo, quality, s=85, color=COLORS[i], marker=shapes[i], edgecolors="white", linewidth=.8, zorder=3)
            point.set_gid("quality-cost-point" + (f"-{suffix}" if suffix else "") + f"-{i}")
            y = bottom + height - .015 - i * height / 4
            fig.add_artist(Line2D([.642], [y+.007], linestyle="none", marker=shapes[i], markersize=7,
                                  color=COLORS[i], transform=fig.transFigure))
            fig.text(.665, y, f"{LABELS[i]} / {row['effort']}", fontsize=11, weight="bold", color=COLORS[i])
            line_gap = .017 if invoice else .026
            fig.text(.665, y-line_gap, f"Score: {quality:.2f} / 100", fontsize=9.5)
            cost_label = currency(lo) + (f"–{currency(hi)}" if hi > lo else "")
            fig.text(.665, y-2*line_gap, f"Recorded spend: {cost_label}", fontsize=9.5)
            fig.text(.665, y-3*line_gap, f"Completed: {row['completed']:,}/{row['planned']:,} ({row['completion_percent']:.2f}%)", fontsize=9.5)
            if hi > lo:
                fig.text(.665, y-4*line_gap, f"{row['missing_usage_attempts']} attempts missing usage", fontsize=9, color="#526170")
    footer_y = .083 if invoice else .12
    fig.text(.045, footer_y, "Points use metered spend; horizontal bars extend to the conservative upper estimate for missing usage, not a confidence interval.", fontsize=9)
    fig.text(.045, footer_y-.026, "Completion is distinct from quality. F1 is not document accuracy. Point estimates have differing statistical support; see the full results.", fontsize=9)
    fig.text(.045, footer_y-.052, "Descriptive cross-platform results; run conditions differ across reasoning settings. Costs include recorded retries. No universal ranking is inferred.", fontsize=9, color="#526170")
    return fig


def summary_figure(plt, data):
    conditions = [(DIRECT, "omitted"), *[(m, e) for m in MODELS[1:] for e in ("none", "low", "high")]]
    totals = []
    for model, effort in conditions:
        rows = [r for r in data["cells"] if r["model_id"] == model and r["effort"] == effort]
        totals.append({"label": f"{LABELS[MODELS.index(model)]} / {effort}", "color": COLORS[MODELS.index(model)],
                       **{k: sum(r[k] for r in rows) for k in ("planned", "completed", "cost_lower_usd", "cost_upper_usd")}})
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 8.3))
    fig.subplots_adjust(left=.15, right=.965, top=.77, bottom=.19, wspace=.50)
    fig.text(.045, .95, "Benchmark runs at a glance", fontsize=22, weight="bold")
    fig.text(.045, .904, "12 workloads  ·  4,796 planned observations per configuration  ·  10 configurations", fontsize=12)
    fig.text(.045, .861, "GPT-4.1: direct OpenAI API    •    GPT-5.6: Amazon Bedrock Mantle    •    August 26–30, 2026", fontsize=10, color="#526170")
    for ax in axes:
        style_axis(ax, [r["label"] for r in totals])
    axes[0].set_title("API completion", loc="left", pad=18)
    axes[0].set_xlim(0, 105)
    axes[0].set_xticks([0, 25, 50, 75, 100])
    axes[0].set_xlabel("Completed / planned (%)")
    axes[1].set_title("Recorded spend and accounting bounds", loc="left", pad=18)
    axes[1].set_xlim(0, max(r["cost_upper_usd"] for r in totals) * 1.10)
    axes[1].set_xlabel("USD · includes recorded retries")
    for i, row in enumerate(totals):
        rate = 100 * row["completed"] / row["planned"]
        axes[0].barh(i, 100, height=.48, color="#edf0f4")
        axes[0].barh(i, rate, height=.48, color=row["color"])
        axes[0].text(.015, i + .29, f"{row['completed']:,}/{row['planned']:,}  ·  {rate:.2f}%  ·  gap {row['planned']-row['completed']}", transform=axes[0].get_yaxis_transform(), fontsize=8.8, va="top")
        lo, hi = row["cost_lower_usd"], row["cost_upper_usd"]
        axes[1].barh(i, lo, height=.48, color=row["color"])
        if hi > lo:
            axes[1].barh(i, hi-lo, left=lo, height=.48, color="#f2eaf8", edgecolor=row["color"], hatch="////", linewidth=.7)
        axes[1].text(.015, i + .29, currency(lo) + (f"–{currency(hi)}*" if hi > lo else ""), transform=axes[1].get_yaxis_transform(), fontsize=8.8, va="top")
    fig.text(.045, .126, "Completion is not answer correctness. Banking77 supplies 64.22% of cases; inspect each workload for gaps. No pooled latency or quality score.", fontsize=9)
    fig.text(.045, .091, "* Sol/high: four ExtractBench attempts lack usage. Hatching extends metered spend to its conservative upper estimate, not a confidence interval.", fontsize=9)
    fig.text(.045, .056, "Descriptive cross-platform results. Reasoning settings differ in run timing, some output ceilings and overlapping execution; see methodology.", fontsize=9, color="#526170")
    return fig


def svg_text(fig, title, namespace=None):
    stream = io.StringIO()
    fig.savefig(stream, format="svg", metadata={"Date": None, "Creator": "Benchmark chart renderer"})
    svg = stream.getvalue()
    svg = svg[svg.index("<svg"):]
    svg = re.sub(r'width="[^"]+" height="[^"]+"', '', svg, count=1)
    svg = svg.replace('version="1.1">', 'version="1.1" role="img" aria-labelledby="figure-title">\n<title id="figure-title">' + html.escape(title) + '</title>\n<style>text { font-family: "DejaVu Sans", Arial, Helvetica, sans-serif !important; }</style>', 1)
    if namespace:
        # The explorer embeds two SVGs. Keep their clip paths, markers and
        # accessible titles separate while retaining the tested point IDs.
        for old in set(re.findall(r'\bid="([^"]+)"', svg)):
            if old.startswith("quality-cost-"):
                continue
            new = namespace + old
            svg = svg.replace(f'id="{old}"', f'id="{new}"').replace(f'url(#{old})', f'url(#{new})')
            svg = svg.replace(f'href="#{old}"', f'href="#{new}"').replace(f'aria-labelledby="{old}"', f'aria-labelledby="{new}"')
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", required=True, help="Write the charts; never alters measurement inputs or manifest")
    parser.parse_args()
    data = chart_data()
    plt = setup_plotting()
    figures, quality_cost_figures = {}, {}
    charts = PACKAGE / "charts"
    charts.mkdir(exist_ok=True)
    summary = summary_figure(plt, data)
    summary.savefig(charts / "operating-summary.png", dpi=150, metadata={"Software": "Benchmark chart renderer"})
    (charts / "operating-summary.svg").write_text(svg_text(summary, "All benchmark configurations: API completion and recorded spend"))
    plt.close(summary)
    for benchmark, title in WORKLOADS:
        for effort in ("none", "low", "high"):
            fig = workload_figure(plt, data, benchmark, title, effort)
            figures[f"{benchmark}|{effort}"] = svg_text(fig, f"{title}, GPT-5.6 {effort}: TTFT, duration, completion and recorded spend")
            if benchmark == "classification-routing" and effort == "none":
                fig.savefig(charts / "workload-example.png", dpi=150, metadata={"Software": "Benchmark chart renderer"})
            plt.close(fig)
            fig = quality_cost_figure(plt, data, benchmark, title, effort)
            quality_svg = svg_text(fig, f"Quality versus cost: {title}, GPT-5.6 {effort}", namespace="qc-")
            quality_cost_figures[f"{benchmark}|{effort}"] = quality_svg
            if benchmark == "classification-routing" and effort == "none":
                fig.savefig(charts / "quality-cost-example.png", dpi=150, metadata={"Software": "Benchmark chart renderer"})
                (charts / "quality-cost-example.svg").write_text(quality_svg)
            plt.close(fig)
    data_text = json.dumps(data, indent=2) + "\n"
    (charts / "chart-data.json").write_text(data_text)
    template = (PACKAGE / "chart-template.html").read_text()
    # Escape HTML-sensitive characters inside inert JSON script blocks.
    replacements = {"@@DATA@@": data_text.replace("<", "\\u003c"),
                    "@@FIGURES@@": json.dumps(figures).replace("<", "\\u003c"),
                    "@@QUALITY_COST_FIGURES@@": json.dumps(quality_cost_figures).replace("<", "\\u003c"),
                    "@@WORKLOADS@@": "".join(f'<option value="{key}">{html.escape(label)}</option>' for key, label in WORKLOADS),
                    "@@INITIAL@@": figures["classification-routing|none"],
                    "@@QUALITY_COST_INITIAL@@": quality_cost_figures["classification-routing|none"]}
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    (PACKAGE / "CHARTS.html").write_text(template)
    print("WROTE offline explorer: 12 workloads × 3 reasoning views, preserving all 120 result cells")
    print("WROTE static overview, example, and chart-data.json; measurement files and manifest unchanged")


if __name__ == "__main__":
    main()
