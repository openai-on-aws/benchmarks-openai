"""
Build the blog-post charts from result JSONs in quality/results/.

Every number in every chart is read from a result file at build time — nothing
is hardcoded. Usage:

  python docs/img/build_blog_charts.py
"""

import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(__file__)
RESULTS = os.path.join(HERE, "..", "..", "quality", "results")

# Categorical palette, validated (all-pairs CVD dE 24.7 light / 26.8 dark,
# normal dE 31.8+, contrast >=3:1 on both surfaces)
C_BEDROCK = "#2a78d6"
C_OPENAI = "#eb6834"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

ARMS = [  # (short label, backend, model-id substring, is_bedrock)
    ("luna", "mantle", "gpt-5.6-luna", True),
    ("terra", "mantle", "gpt-5.6-terra", True),
    ("sol", "mantle", "gpt-5.6-sol", True),
    ("mini", "saas", "gpt-5.4-mini", False),
    ("nano", "saas", "gpt-5.4-nano", False),
]


def latest(pattern, backend, model_sub):
    best = None
    for path in glob.glob(os.path.join(RESULTS, pattern)):
        with open(path) as f:
            d = json.load(f)
        if d.get("backend") != backend or model_sub not in d.get("model", ""):
            continue
        if best is None or d["timestamp"] > best["timestamp"]:
            best = d
    if best is None:
        raise FileNotFoundError(f"{pattern} {backend} {model_sub}")
    return best


def style_ax(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def hbars(ax, labels, values, colors, fmt, xmax=None):
    y = range(len(labels))[::-1]
    bars = ax.barh(list(y), values, height=0.55, color=colors, zorder=3)
    for patch in bars:  # 4px-radius rounded data end, square baseline
        patch.set_linewidth(0)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, color=SECONDARY, fontsize=10)
    span = xmax or max(values)
    for yi, v in zip(y, values):
        ax.text(v + span * 0.02, yi, fmt(v), va="center", ha="left",
                color=INK, fontsize=9.5, fontweight="semibold")
    ax.set_xlim(0, span * 1.18)


def legend(fig):
    fig.legend(
        handles=[plt.Rectangle((0, 0), 1, 1, color=C_BEDROCK, label="OpenAI models on Amazon Bedrock"),
                 plt.Rectangle((0, 0), 1, 1, color=C_OPENAI, label="OpenAI API")],
        loc="lower center", ncol=2, frameon=False, fontsize=9,
        labelcolor=SECONDARY, bbox_to_anchor=(0.5, 0.0))


def save(fig, name):
    out = os.path.join(HERE, name)
    fig.savefig(out, dpi=200, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out}")


# ---- Chart 1: AIME — the leaderboard and the invoice disagree ----------------

def chart_accuracy_vs_cost():
    rows = []
    for label, backend, sub, is_br in ARMS:
        d = latest("quickeval_aime_*.json", backend, sub)
        s = d["summary"]
        rows.append((label, s["accuracy"], s["cost_per_success_usd"],
                     C_BEDROCK if is_br else C_OPENAI))
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.2))
    fig.patch.set_facecolor(SURFACE)
    labels = [r[0] for r in rows]
    colors = [r[3] for r in rows]
    style_ax(axes[0]); style_ax(axes[1])
    hbars(axes[0], labels, [r[1] * 100 for r in rows], colors, lambda v: f"{v:.0f}%")
    axes[0].set_title("Accuracy — AIME 2022–24 (n=60)", fontsize=10.5,
                      color=INK, loc="left", pad=10)
    hbars(axes[1], labels, [r[2] for r in rows], colors, lambda v: f"${v:.4f}")
    axes[1].set_title("Cost per correct answer", fontsize=10.5,
                      color=INK, loc="left", pad=10)
    legend(fig)
    fig.suptitle("The leaderboard and the invoice rank models differently",
                 fontsize=12, color=INK, x=0.02, ha="left", y=1.04, fontweight="bold")
    fig.subplots_adjust(bottom=0.18, wspace=0.35)
    save(fig, "chart1_accuracy_vs_cost.png")


# ---- Chart 2: DeepSearchQA — turns compound into tokens and dollars ----------

def chart_turns_compound():
    rows = []
    for label, backend, sub, is_br in ARMS:
        d = latest("deepsearchqa_*_judged.json", backend, sub)
        s, js = d["summary"], d["judge_summary"]
        n_pass = round(js["pass_rate"] * s["n"])
        rows.append((label, s["mean_turns"], s["mean_input_tokens"] / 1000,
                     s["total_cost_usd"] / n_pass, js["mean_f1"],
                     C_BEDROCK if is_br else C_OPENAI))
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.4))
    fig.patch.set_facecolor(SURFACE)
    ax = axes[0]
    style_ax(ax)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    for label, turns, ktok, _, _, color in rows:
        ax.scatter(turns, ktok, s=110, color=color, zorder=3,
                   edgecolors=SURFACE, linewidths=2)
        ax.annotate(label, (turns, ktok), textcoords="offset points",
                    xytext=(0, 9), ha="center", color=INK, fontsize=9.5,
                    fontweight="semibold")
    ax.set_xlabel("Mean agent turns per question", color=MUTED, fontsize=9)
    ax.set_ylabel("Input tokens per question (k)", color=MUTED, fontsize=9)
    ax.set_ylim(0, max(r[2] for r in rows) * 1.25)
    ax.set_title("More turns → context re-sent every turn", fontsize=10.5,
                 color=INK, loc="left", pad=10)
    ax2 = axes[1]
    style_ax(ax2)
    labels = [r[0] for r in rows]
    colors = [r[5] for r in rows]
    hbars(ax2, labels, [r[3] for r in rows], colors, lambda v: f"${v:.2f}")
    ax2.set_title("Cost per passing answer (F1 ≥ 0.7)", fontsize=10.5,
                  color=INK, loc="left", pad=10)
    legend(fig)
    fig.suptitle("DeepSearchQA (live web research, n=20): turn efficiency beats token price",
                 fontsize=12, color=INK, x=0.02, ha="left", y=1.04, fontweight="bold")
    fig.subplots_adjust(bottom=0.22, wspace=0.35)
    save(fig, "chart2_deepsearchqa_turns.png")


# ---- Chart 3: GDPval — quality gap on real deliverables ----------------------

def chart_gdpval():
    rows = []
    for label, backend, sub, is_br in ARMS:
        d = latest("gdpval_*_judged.json", backend, sub)
        js = d["judge_summary"]
        graded = js["n_graded"]
        passed = round(js["pass_rate"] * graded)
        rows.append((label, passed, graded, js["cost_per_pass_usd"],
                     C_BEDROCK if is_br else C_OPENAI))
    fig, ax = plt.subplots(figsize=(9.2, 3.0))
    fig.patch.set_facecolor(SURFACE)
    style_ax(ax)
    labels = [r[0] for r in rows]
    colors = [r[4] for r in rows]
    vals = [r[1] / r[2] * 100 for r in rows]
    hbars(ax, labels, vals, colors,
          lambda v: "", xmax=100)
    y = range(len(labels))[::-1]
    for yi, (label, passed, graded, cpp, _) in zip(y, rows):
        v = passed / graded * 100
        ax.text(v + 2, yi, f"{passed}/{graded} pass · ${cpp:.3f} per passing deliverable",
                va="center", ha="left", color=INK, fontsize=9.5, fontweight="semibold")
    ax.set_xlim(0, 100)
    ax.set_xlabel("Deliverables passing the rubric bar (≥0.7 weighted rubric points), %",
                  color=MUTED, fontsize=9)
    legend(fig)
    fig.suptitle("GDPval (real professional deliverables, n=24): quality is the differentiator",
                 fontsize=12, color=INK, x=0.02, ha="left", y=1.06, fontweight="bold")
    fig.subplots_adjust(bottom=0.3)
    save(fig, "chart3_gdpval.png")


if __name__ == "__main__":
    chart_accuracy_vs_cost()
    chart_turns_compound()
    chart_gdpval()
