"""Portable views and bounded evidence inspection for saved benchmark runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from . import __version__
from .config import fingerprint
from .runners import redact

MAX_INLINE_BYTES = 1_000_000
PREVIEW_BYTES = 16_384
TEXT_SUFFIXES = {".json", ".jsonl", ".txt", ".log", ".md", ".yaml", ".yml", ".toml", ".patch", ".diff"}
ATTEMPT_FIELDS = (
    "attempt_id", "task_id", "repetition", "status", "success", "target", "runner_version",
    "cost_usd", "known_cost_subtotal_usd", "cost_basis", "accounting_complete",
    "wall_seconds", "agent_wall_seconds", "usage", "usage_granularity", "grading",
    "errors", "observed_models", "upstream_providers", "trace_sha256",
)


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON value: {value}")


def load_run(path):
    """Accept saved runs only; reading one never launches a benchmark."""
    value = json.loads(Path(path).read_text(), parse_constant=_reject_constant)
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("run_id"), str)
            or not isinstance(value.get("attempts"), list)
            or any(not isinstance(row, dict) for row in value["attempts"])):
        raise ValueError("Expected a schema_version=1 run.json with run_id and attempts")
    return value


def list_runs(directory):
    """Discover immediate run directories without traversing archived copies."""
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Results directory does not exist: {directory}")
    paths = [directory / "run.json", *sorted(directory.glob("*/run.json"))]
    runs, unreadable = [], []
    for path in paths:
        if not path.is_file():
            continue
        try:
            if not path.resolve().is_relative_to(directory):
                raise ValueError("Run file resolves outside the results directory")
            run = load_run(path)
            runs.append({
                "run_id": run["run_id"], "name": run.get("name"), "path": str(path.resolve()),
                "status": run.get("status"), "started_at": run.get("started_at"),
                "synthetic": run.get("synthetic"), "validation_only": run.get("validation_only", False),
                "suite": run.get("experiment", {}).get("suite", "starter"),
                "recorded_attempts": len(run["attempts"]),
                "successes": sum(row.get("success") is True for row in run["attempts"]),
                "protocol_hash": run.get("protocol_hash"),
            })
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            unreadable.append({"path": str(path), "error": str(exc)})
    runs.sort(key=lambda run: (run["started_at"] or "", run["run_id"]), reverse=True)
    return {"runs": runs, "unreadable": unreadable}


def _target_key(target, runner_version):
    identity = {key: value for key, value in target.items() if key != "id"}
    identity["runner_version"] = runner_version
    return fingerprint(identity)


def _evidence_path(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError("No evidence path was recorded")
    path = Path(relative)
    if (path.is_absolute() or ".." in path.parts or "\\" in relative
            or ":" in relative or "\0" in relative):
        raise ValueError("Evidence path must stay inside the run directory")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Evidence path resolves outside the run directory")
    return resolved


def evidence_references(row, source, *, report_directory=None, previews=False):
    """Resolve only recorded evidence, never arbitrary paths supplied by traces."""
    root = Path(source).expanduser().resolve().parent if source else None
    candidates = [("Trace", row.get("trace")), ("Workspace", row.get("workspace"))]
    upstream = row.get("upstream") or {}
    if upstream.get("result"):
        candidates.append(("Upstream result", f"attempts/{row['attempt_id']}/{upstream['result']}"))
    evidence = []
    for label, relative in candidates:
        if relative is None:
            continue
        item = {"label": label, "relative_path": str(relative), "status": "source_unavailable"}
        evidence.append(item)
        if root is None:
            continue
        try:
            path = _evidence_path(root, relative)
            if not path.exists():
                item["status"] = "missing"
                continue
            item.update(status="available", path=str(path), kind="directory" if path.is_dir() else "file")
            if report_directory is not None:
                # Generate a relative URL ourselves; recorded strings never become hrefs.
                relative_url = os.path.relpath(path, Path(report_directory).resolve())
                item["href"] = "./" + quote(Path(relative_url).as_posix(), safe="/")
            if previews and path.is_file():
                item["bytes"] = path.stat().st_size
                if path.suffix.lower() in TEXT_SUFFIXES:
                    with path.open("rb") as stream:
                        raw = stream.read(PREVIEW_BYTES + 1)
                    item["preview"] = redact(raw[:PREVIEW_BYTES].decode("utf-8", errors="replace"))
                    item["truncated"] = len(raw) > PREVIEW_BYTES
        except (OSError, ValueError) as exc:
            item.update(status="unavailable", reason=str(exc))
    return evidence


def inspect_attempt(source, attempt_id):
    run = load_run(source)
    rows = [row for row in run["attempts"] if row.get("attempt_id") == attempt_id]
    if len(rows) != 1:
        raise ValueError(f"Expected one attempt named {attempt_id!r}; found {len(rows)}")
    row = rows[0]
    return {
        "schema_version": 1,
        "run": {
            "run_id": run["run_id"], "source": str(Path(source).expanduser().resolve()),
            "status": run.get("status"), "synthetic": run.get("synthetic"),
            "validation_only": run.get("validation_only", False), "protocol_hash": run.get("protocol_hash"),
        },
        "attempt": {key: row[key] for key in ATTEMPT_FIELDS if key in row},
        "evidence": evidence_references(row, source, previews=True),
        "cost_scope": "Inference only; infrastructure, subscriptions, external tools, and judges are excluded.",
    }


def explorer_data(runs, summary, directory, sources=None):
    if sources is None:
        candidate = Path(directory) / "run.json"
        same_run = (len(runs) == 1 and candidate.is_file()
                    and load_run(candidate)["run_id"] == runs[0]["run_id"])
        sources = [candidate] if same_run else [None] * len(runs)
    if len(sources) != len(runs):
        raise ValueError("Provide one source path for each run")
    targets = []
    for target in summary["targets"]:
        identity = target["target"]
        targets.append({**target, "key": _target_key(identity, identity["runner_version"])})
    tasks = []
    for task in summary["tasks"]:
        identity = task["target"]
        tasks.append({**task, "target_key": _target_key(identity, identity["runner_version"])})
    attempts, provenance = [], []
    for run, source in zip(runs, sources):
        source = str(Path(source).expanduser().resolve()) if source is not None else None
        provenance.append({
            "run_id": run["run_id"], "name": run.get("name"), "source": source,
            "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
            "suite": run.get("experiment", {}).get("suite", "starter"),
        })
        for row in run["attempts"]:
            attempts.append({
                **{key: row[key] for key in ATTEMPT_FIELDS if key in row},
                "key": fingerprint([run["run_id"], row["attempt_id"]]),
                "target_key": _target_key(row["target"], row["runner_version"]),
                "run_id": run["run_id"], "source": source,
                "evidence": evidence_references(row, source, report_directory=directory),
            })
    return {
        "schema_version": 1, "plugin_version": __version__,
        "view_id": fingerprint([summary["protocol_hash"], summary["run_ids"]]),
        "synthetic": summary["synthetic"], "validation_only": summary["validation_only"],
        "protocol_hash": summary["protocol_hash"], "scope": summary["scope"],
        "runs": provenance, "targets": targets, "tasks": tasks, "attempts": attempts,
    }


def render_explorer(runs, summary, directory, sources=None, *, inline=False):
    data = explorer_data(runs, summary, directory, sources)
    data["inline"] = inline
    # JSON embedded in a script element must not be able to terminate that element.
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = Path(__file__).resolve().parents[2] / "assets/report.html"
    fragment = template.read_text().replace("__BEDROCK_BENCH_DATA__", encoded)
    if inline:
        if len(fragment.encode("utf-8")) > MAX_INLINE_BYTES:
            raise ValueError("Report exceeds the 1 MB inline limit; use --format html to inspect the full report")
        return fragment
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><title>Bedrock Bench results</title>'
        '<style>body{margin:0;padding:24px;background:light-dark(#f7f7f8,#131415)}'
        '@media(max-width:600px){body{padding:10px}}</style></head><body>\n'
        + fragment + "\n</body></html>\n"
    )
