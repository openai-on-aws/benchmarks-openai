"""Create a protocol-checked comparison of CyberSOCEval result files."""

import argparse
import json
import statistics
from itertools import combinations
from pathlib import Path


PROTOCOL_FIELDS = (
    "benchmark",
    "input_profile",
    "max_output_tokens",
    "structured_output",
    "seed",
)


def protocol_signature(payload):
    signature = {field: payload.get(field) for field in PROTOCOL_FIELDS}
    signature["dataset_commit"] = (payload.get("dataset") or {}).get("commit")
    signature["sample_ids"] = [row.get("id") for row in payload.get("results", [])]
    return signature


def mean(values):
    present = [value for value in values if value is not None]
    return statistics.mean(present) if present else None


def summarize(payload):
    rows = payload.get("results") or []
    exact = sum(bool(row.get("exact")) for row in rows)
    total_cost = (payload.get("summary") or {}).get("estimated_total_cost_usd")
    return {
        "label": f"{payload.get('backend')}/{payload.get('model')}",
        "model": payload.get("model"),
        "backend": payload.get("backend"),
        "n": len(rows),
        "exact_count": exact,
        "exact_accuracy": exact / len(rows) if rows else None,
        "mean_jaccard": mean(row.get("jaccard") for row in rows),
        "mean_f1": mean(row.get("f1") for row in rows),
        "mean_latency_ms": mean(row.get("latency_ms") for row in rows),
        "errors": sum(row.get("error") is not None for row in rows),
        "refusals": sum(bool(row.get("refusal")) for row in rows),
        "incomplete": sum(row.get("status") == "incomplete" for row in rows),
        "estimated_total_cost_usd": total_cost,
        "estimated_cost_per_attempt_usd": (
            total_cost / len(rows) if total_cost is not None and rows else None
        ),
        "estimated_cost_per_exact_usd": (
            total_cost / exact if total_cost is not None and exact else None
        ),
    }


def compare_payloads(payloads):
    if len(payloads) < 2:
        raise ValueError("at least two result files are required")
    expected = protocol_signature(payloads[0])
    mismatches = []
    for payload in payloads[1:]:
        actual = protocol_signature(payload)
        differing = [
            field for field in expected if expected[field] != actual[field]
        ]
        if differing:
            mismatches.append(
                {
                    "model": payload.get("model"),
                    "fields": differing,
                }
            )
    if mismatches:
        details = "; ".join(
            f"{item['model']}: {', '.join(item['fields'])}"
            for item in mismatches
        )
        raise ValueError(f"incompatible protocols: {details}")

    arms = [summarize(payload) for payload in payloads]
    by_label = {
        f"{payload.get('backend')}/{payload.get('model')}": {
            row["id"]: row for row in payload.get("results") or []
        }
        for payload in payloads
    }
    pairwise = []
    for arm_a, arm_b in combinations(arms, 2):
        rows_a = by_label[arm_a["label"]]
        rows_b = by_label[arm_b["label"]]
        a_wins = b_wins = ties = 0
        for sample_id in expected["sample_ids"]:
            delta = rows_a[sample_id].get("jaccard", 0) - rows_b[sample_id].get(
                "jaccard", 0
            )
            if delta > 0:
                a_wins += 1
            elif delta < 0:
                b_wins += 1
            else:
                ties += 1
        pairwise.append(
            {
                "arm_a": arm_a["label"],
                "arm_b": arm_b["label"],
                "a_jaccard_wins": a_wins,
                "b_jaccard_wins": b_wins,
                "ties": ties,
            }
        )
    return {
        "protocol": {
            key: value for key, value in expected.items() if key != "sample_ids"
        },
        "arms": arms,
        "pairwise": pairwise,
    }


def percent(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def decimal(value, digits=3):
    return "—" if value is None else f"{value:.{digits}f}"


def money(value):
    return "—" if value is None else f"${value:.4f}"


def render_markdown(report):
    protocol = report["protocol"]
    lines = [
        "# CyberSOCEval comparison",
        "",
        (
            f"Protocol: `{protocol['input_profile']}` reports, seed "
            f"`{protocol['seed']}`, max output `{protocol['max_output_tokens']}`, "
            f"structured output `{protocol['structured_output']}`, dataset "
            f"`{protocol['dataset_commit']}`."
        ),
        "",
        (
            "| Model | Exact | Jaccard | F1 | Latency | Cost | Cost/task | "
            "Cost/exact | Errors | Refusals | Incomplete |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in report["arms"]:
        lines.append(
            "| {label} | {count}/{n} ({accuracy}) | {jaccard} | {f1} | "
            "{latency} ms | {cost} | {cost_task} | {cost_exact} | {errors} | "
            "{refusals} | {incomplete} |".format(
                label=arm["label"],
                count=arm["exact_count"],
                n=arm["n"],
                accuracy=percent(arm["exact_accuracy"]),
                jaccard=decimal(arm["mean_jaccard"]),
                f1=decimal(arm["mean_f1"]),
                latency=decimal(arm["mean_latency_ms"], 1),
                cost=money(arm["estimated_total_cost_usd"]),
                cost_task=money(arm["estimated_cost_per_attempt_usd"]),
                cost_exact=money(arm["estimated_cost_per_exact_usd"]),
                errors=arm["errors"],
                refusals=arm["refusals"],
                incomplete=arm["incomplete"],
            )
        )

    lines.extend(["", "## Paired Jaccard outcomes", ""])
    for pair in report["pairwise"]:
        lines.append(
            f"- `{pair['arm_a']}` vs `{pair['arm_b']}`: "
            f"{pair['a_jaccard_wins']}–{pair['b_jaccard_wins']}, "
            f"{pair['ties']} ties."
        )
    lines.extend(
        [
            "",
            "Cost per exact answer is intentionally left undefined when an arm "
            "has no exact successes. Small pilots validate the harness and must "
            "not be presented as model rankings.",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Compare protocol-compatible CyberSOCEval JSON results"
    )
    parser.add_argument("results", nargs="+")
    parser.add_argument(
        "--out",
        default="quality/results/CYBERSOC_COMPARISON.md",
    )
    args = parser.parse_args()
    payloads = [json.loads(Path(path).read_text()) for path in args.results]
    try:
        report = compare_payloads(payloads)
    except ValueError as error:
        parser.error(str(error))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(report))
    print(f"Compared {len(payloads)} compatible result files")
    print(f"Markdown: {output}")


if __name__ == "__main__":
    main()
