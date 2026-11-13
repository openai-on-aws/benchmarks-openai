"""Render a portable, offline dashboard from saved evaluation evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).with_name("dashboard.html")
PLACEHOLDER = "__PROMPT_OPTIMIZER_DATA__"
LABELS = {
    "source_original": "Source · original prompt",
    "target_original": "Target · original prompt",
    "target_selected": "Target · selected prompt",
    "target_challenger": "Target · candidate prompt",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def discover_reports(project: Path) -> list[Path]:
    """Read only the documented report locations, never arbitrary descendants."""
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ValueError("Choose a directory containing saved report.json evidence.")
    direct = project / "report.json"
    nested = [project / name / "report.json" for name in ("local", "agentcore", "results")]
    nested = [path for path in nested if path.is_file()]
    if direct.is_file() and nested:
        raise ValueError("Both root and nested reports exist. Choose a single run directory or a project containing only backend reports.")
    found = [direct] if direct.is_file() else nested
    if not found:
        raise ValueError("No saved reports found. Use demo, run locally, or download completed AgentCore results first.")
    resolved = [path.resolve() for path in found]
    if len(set(resolved)) != len(resolved):
        raise ValueError("The same saved report appears more than once. Choose one copy.")
    return resolved


def _is_bundled_recording(path: Path, backend: str) -> bool:
    """Only byte-identical bundled measurements receive the historical label."""
    if backend not in ("local", "agentcore"):
        return False
    original = PLUGIN_ROOT / "examples/recorded-invoice-comparison" / backend / "report.json"
    return original.is_file() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest()


def _provenance(path: Path, report: dict, historical: bool) -> str:
    if historical:
        return "Recorded live measurements from 13 November 2026. Synthetic inputs and unreviewed reference answers; this is not production accuracy."
    unavailable = "Label provenance is unavailable in this saved report. Review the source dataset and reference answers before treating check agreement as accuracy."
    frozen = path.parent / "run-input.json"
    if frozen.is_file():
        try:
            config = read_json(frozen)
            if not isinstance(config, dict):
                return unavailable
            canonical = json.dumps({key: value for key, value in config.items() if key != "fingerprint"},
                                   sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                   allow_nan=False).encode("utf-8")
        except (OSError, ValueError, TypeError):
            return "The optional frozen input manifest could not be verified. " + unavailable
        verified = hashlib.sha256(canonical).hexdigest()
        if report.get("fingerprint") and config.get("fingerprint") == report["fingerprint"] == verified:
            document = config.get("document")
            datasets = document.get("datasets") if isinstance(document, dict) else None
            if isinstance(datasets, list) and datasets and all(isinstance(d, dict) for d in datasets):
                origins = sorted({str(d.get("provenance") or "unspecified") for d in datasets})
                labels = sorted({str(d.get("label_status") or "unspecified") for d in datasets})
                return "Frozen dataset declarations: " + ", ".join(origins) + " inputs; reference labels " + ", ".join(labels) + ". Review the references before drawing conclusions."
    return unavailable


def _trial(row: dict) -> dict:
    result = row.get("result") or {}
    return {
        "id": row.get("id") or result.get("trial_id"),
        "case_id": row.get("case_id"),
        "repeat": row.get("repeat"),
        "status": result.get("status", "unknown"),
        "outputs": result.get("outputs") or [],
        "inputs": [turn.get("user") for turn in result.get("turn_records") or []],
        "turn_records": result.get("turn_records") or [],
        "checks": row.get("checks") or {},
        "timing": result.get("timing") or {},
        "latency_ms": result.get("latency_ms"),
        "route_calls": result.get("route_calls") or [],
        # Per-attempt prices cannot be reconstructed from tokens alone.
        "cost_usd": result.get("cost_usd"),
    }


def _report(path: Path, index: int) -> dict:
    raw = read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("holdout"), dict):
        raise ValueError(f"{path.name} is not an evaluation report with a holdout section.")
    if not isinstance(raw.get("simulated"), bool):
        raise ValueError("A saved report must declare whether its measurements are simulated.")
    backend = str(raw.get("backend") or "unknown")
    groups = []
    for name, group in raw["holdout"].items():
        # Keep a declared but unfinished configuration visible as unscored.
        # Omitting it would make the remaining configurations look complete.
        group = {} if group is None else group
        if not isinstance(group, dict):
            raise ValueError(f"Holdout configuration {name} must be an object or null.")
        if group.get("reused_from"):
            continue
        rows = group.get("trials") or []
        models = list(dict.fromkeys(
            str(call["model_requested"])
            for row in rows
            for call in (row.get("result") or {}).get("route_calls", [])
            if call.get("model_requested")
        ))
        if not models:
            models = list(dict.fromkeys(
                str(row["result"]["model_requested"]) for row in rows
                if (row.get("result") or {}).get("model_requested")
            ))
        if not models:
            models = list(dict.fromkeys(str(row["model_id"]) for row in rows if row.get("model_id")))
        groups.append({
            "id": name,
            "label": LABELS.get(name, name.replace("_", " ")),
            "model": ", ".join(models) or "Model identity unavailable",
            "score": group.get("score") or {},
            "trials": [_trial(row) for row in rows],
        })
    historical = _is_bundled_recording(path, backend)
    return {
        "id": f"report-{index}",
        "backend": backend,
        "fingerprint": raw.get("fingerprint"),
        "simulated": raw["simulated"],
        "status": raw.get("evaluation_status", "unknown"),
        "source_name": f"{path.parent.name}/{path.name}",
        "historical": historical,
        "provenance": _provenance(path, raw, historical),
        "model_costs": raw.get("model_costs") or {},
        # An absent ledger means unknown coverage, not zero unresolved attempts.
        "unresolved_attempts": raw.get("unresolved_attempts"),
        "grading_error_records": raw.get("grading_error_records"),
        "cost_note": raw.get("cost_note") or "Missing usage or pricing remains unknown. Model costs exclude infrastructure and human review.",
        "latency_scope": raw.get("latency_scope") or "Consult the saved timing scope before comparing latency.",
        "claim": raw.get("claim") or "Results apply only to the evaluated cases, settings and routes.",
        "selected_prompt": raw.get("selected_prompt") or {},
        "groups": groups,
    }


def build_data(project: Path, *, title: str | None = None) -> dict:
    paths = discover_reports(Path(project))
    reports = [_report(path, index) for index, path in enumerate(paths, 1)]
    if len(reports) > 1:
        fingerprints = {report["fingerprint"] for report in reports}
        if any(not value for value in fingerprints) or len(fingerprints) != 1:
            raise ValueError("Reports have missing or different input fingerprints. Open each run separately; they cannot be presented as one matched comparison.")
        if len({report["simulated"] for report in reports}) != 1:
            raise ValueError("Simulated and live measurements cannot share a comparison dashboard.")
        if len({report["backend"] for report in reports}) != len(reports):
            raise ValueError("Multiple reports use the same backend. Choose one run per backend to avoid double-counting.")
    historical = all(report["historical"] for report in reports)
    provenance = list(dict.fromkeys(report["provenance"] for report in reports))
    return {
        "title": title or ("Recorded model comparison" if historical else "Model comparison"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical": historical,
        "provenance": " ".join(provenance),
        "reports": reports,
    }


def render_dashboard(project: Path, output: Path, *, title: str | None = None, force: bool = False) -> dict:
    output = Path(output).expanduser().resolve()
    if output.is_relative_to(PLUGIN_ROOT):
        raise ValueError("Choose a dashboard output outside the installed plugin.")
    if output.exists() and not force:
        raise ValueError("Dashboard output already exists. Choose another filename or use --force to replace it.")
    if output.suffix.lower() not in (".html", ".htm"):
        raise ValueError("Dashboard output must be an .html file.")
    data = build_data(Path(project), title=title)
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("Dashboard template must contain exactly one data placeholder.")
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for character, replacement in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                                    ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(character, replacement)
    html = template.replace(PLACEHOLDER, payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete document; never leave a partially rendered dashboard.
    if force:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(html)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    else:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(html)
    return {
        "status": "dashboard_ready",
        "report": str(output),
        "backends": [report["backend"] for report in data["reports"]],
        "fresh_model_calls": 0,
        "fresh_aws_calls": 0,
        "historical": data["historical"],
        "scope": "Interactive snapshot of saved holdout evidence. Rebuild after downloading or completing a new run.",
    }
