"""Render a portable, offline dashboard from saved evaluation evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import difflib
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).with_name("dashboard.html")
PLACEHOLDER = "__PROMPT_OPTIMIZER_DATA__"
FONT_PLACEHOLDER = "/* __PROMPT_OPTIMIZER_FONTS__ */"
LABELS = {
    "source_original": "Starting model · starting prompt",
    "target_original": "New model · starting prompt",
    "target_selected": "New model · revised prompt",
    "target_challenger": "New model · candidate prompt (not chosen)",
    "target_comparison": "New model · revised prompt for review",
}
COST_METRICS = ("cost_usd_per_session", "cost_usd_per_success")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def embedded_fonts() -> str:
    """Keep the licensed AWS UI fonts and notices inside each offline HTML."""
    directory = TEMPLATE.parent / "fonts"
    notices = "\n\n".join((directory / name).read_text(encoding="utf-8")
                          for name in ("NOTICE", "LICENSE", "CLOUDSCAPE-LICENSE"))
    styles = ["/*\n" + notices.replace("*/", "* /") + "\n*/"]
    for weight, filename in ((400, "open-sans-regular.woff2"),
                             (700, "open-sans-bold.woff2")):
        encoded = base64.b64encode((directory / filename).read_bytes()).decode("ascii")
        styles.append(
            "@font-face{font-family:'Open Sans';font-style:normal;"
            f"font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{encoded}) format('woff2');}}"
        )
    return "\n".join(styles)


def discover_reports(project: Path) -> list[Path]:
    """Read one explicit report or documented locations, never descendants."""
    project = project.expanduser().resolve()
    if project.is_file():
        if project.name not in ("report.json","comparison-report.json"):
            raise ValueError("Choose a report.json or comparison-report.json file, or a directory containing saved evidence.")
        return [project]
    if not project.is_dir():
        raise ValueError("Choose a directory containing saved report.json evidence.")
    comparisons=[project/'comparison-report.json',project/'results/comparison-report.json']
    comparisons=[path for path in comparisons if path.is_file()]
    if comparisons:
        ordinary=[project/'report.json',*[project/name/'report.json' for name in ('local','agentcore','results')]]
        if len(comparisons)!=1 or any(path.is_file() for path in ordinary):
            raise ValueError('Choose one explicit parent or comparison report; cohorts must remain separate.')
        return [comparisons[0].resolve()]
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


def _frozen_input(path: Path, report: dict) -> tuple[dict | None, str]:
    """Use expected answers and policy only when frozen assets match this run."""
    frozen = path.parent / "run-input.json"
    if not frozen.is_file():
        return None, "missing"
    try:
        config = read_json(frozen)
        if not isinstance(config, dict):
            return None, "unverified"
        canonical = json.dumps({key: value for key, value in config.items() if key != "fingerprint"},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                               allow_nan=False).encode("utf-8")
    except (OSError, ValueError, TypeError):
        return None, "unverified"
    verified = hashlib.sha256(canonical).hexdigest()
    if report.get("fingerprint") and config.get("fingerprint") == report["fingerprint"] == verified:
        return config, "verified"
    return None, "unverified"


def _provenance(path: Path, report: dict, historical: bool, config: dict | None, frozen_status: str) -> str:
    assessment = _assessment(config)
    if assessment["mode"] == "source_judge":
        origins = sorted({str(d.get("provenance") or "unspecified") for d in config["document"].get("datasets", []) if isinstance(d, dict)})
        return ("LLM-assessed results against the frozen source text and rubric; no expected answers were supplied or used. "
                "Test data: " + (", ".join(origins) or "unspecified") + ". Needs-review judgments remain in the denominator and do not count as passes. "
                "These are model judgments, not human-verified accuracy.")
    if historical:
        return "Recorded live measurements from 13 November 2026. Synthetic inputs and unreviewed reference answers; this is not production accuracy."
    unavailable = "Label provenance is unavailable in this saved report. Review the source dataset and reference answers before treating check agreement as accuracy."
    if frozen_status == "unverified":
        return "The optional frozen input manifest could not be verified. " + unavailable
    if config is not None:
        document = config.get("document")
        datasets = document.get("datasets") if isinstance(document, dict) else None
        if isinstance(datasets, list) and datasets and all(isinstance(d, dict) for d in datasets):
            origins = sorted({str(d.get("provenance") or "unspecified") for d in datasets})
            labels = sorted({str(d.get("label_status") or "unspecified") for d in datasets})
            if origins == ["synthetic"] and labels == ["unreviewed"]:
                return "These are synthetic test cases. Their expected answers have not been reviewed by a person. Test on reviewed customer examples before deciding to switch."
            return "Test data: " + ", ".join(origins) + " inputs. Expected-answer review status: " + ", ".join(labels) + ". Check the expected answers before using these results to make a production decision."
    return unavailable


def _cost_metric(report: dict, config: dict | None) -> tuple[str, str]:
    selected = report.get("selected_prompt") or {}
    policy = selected.get("policy") or {}
    metric = policy.get("cost_metric")
    source = "selection_policy"
    if metric is None and config is not None:
        metric = config.get("document", {}).get("runner", {}).get("selection", {}).get("cost_metric")
        source = "frozen_input"
    if metric is None:
        metric, source = "cost_usd_per_session", "legacy_default"
    if metric not in COST_METRICS:
        raise ValueError("Saved report has an unsupported cost measure: " + str(metric))
    return metric, source


def _dataset_summary(config: dict | None, historical: bool) -> str:
    """Keep the overview brief; full review status remains in provenance."""
    if historical:
        return "Results from synthetic demo cases."
    document = config.get("document") if isinstance(config, dict) else None
    datasets = document.get("datasets") if isinstance(document, dict) else None
    if (isinstance(datasets, list) and datasets and
            all(isinstance(dataset, dict) and dataset.get("provenance") == "synthetic"
                for dataset in datasets)):
        return "Results from synthetic demo cases."
    return "Test data details are in Run details."


def _cost_values(score: dict) -> dict:
    """Keep zero successes distinct from missing prices; never invent a cost."""
    result = {}
    for key in COST_METRICS:
        value = score.get(key)
        valid = type(value) in (float, int) and math.isfinite(value) and value >= 0
        unpriced = score.get("unpriced_requests")
        if type(unpriced) in (int, float) and unpriced > 0:
            status, value = "unknown", None
        elif (key == "cost_usd_per_success" and score.get("accuracy") == 0
              and type(score.get("n_sessions")) is int and score["n_sessions"] > 0):
            status, value = "no_successes", None
        else:
            status, value = ("known", value) if valid else ("unknown", None)
        result[key] = {"value": value, "status": status}
    return result


def _source_helpers():
    """The same local, stdlib-only projection used by the evaluator."""
    engine = str(PLUGIN_ROOT / "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from customer_eval.source_grading import build_source_context, build_source_criteria, evidence_digest
    return build_source_context, build_source_criteria, evidence_digest


def _assessment(config: dict | None) -> dict:
    """Only verified, explicitly configured source judging may omit references."""
    reference = {"mode": "reference", "quality_label": "Required-check pass rate"}
    if not isinstance(config, dict):
        return reference
    rules = config.get("grading")
    if not isinstance(rules, dict) or not any(isinstance(rule, dict) and rule.get("type") == "llm_source_rubric" for rule in rules.values()):
        return reference
    unavailable = {"mode": "unverified_source_judge", "quality_label": "Assessment configuration unavailable"}
    document = config.get("document")
    definitions = document.get("checks") if isinstance(document, dict) else None
    if (not isinstance(definitions, list) or not definitions or
            any(not isinstance(c, dict) or not isinstance(c.get("id"), str) or type(c.get("required")) is not bool for c in definitions) or
            set(rules) != {c["id"] for c in definitions} or len(definitions) != len(rules)):
        return unavailable
    mixed = document.get("runner", {}).get("grading_reference_mode") == "references_with_source_judges"
    reference_types = ("json_schema", "json_equals", "json_set_equals", "json_unordered_equals")
    allowed_types = ("llm_source_rubric", *reference_types) if mixed else ("llm_source_rubric", "json_schema")
    if mixed and not any(c["required"] and isinstance(rules[c["id"]], dict) and rules[c["id"]].get("type") in reference_types
                         and rules[c["id"]].get("type") != "json_schema" for c in definitions):
        return unavailable
    judges = set()
    for check in definitions:
        rule = rules[check["id"]]
        if not isinstance(rule, dict) or rule.get("type") not in allowed_types:
            return unavailable
        if rule["type"] == "llm_source_rubric":
            if mixed and check["required"]:
                return unavailable
            if (check.get("method") != "llm_judge" or not isinstance(check.get("judge_model_id"), str) or not check["judge_model_id"] or
                    not isinstance(rule.get("rubric"), str) or not rule["rubric"].strip()):
                return unavailable
            judges.add(check["judge_model_id"])
        elif check.get("method") != "deterministic":
            return unavailable
    if len(judges) != 1:
        return unavailable
    try:
        _, criteria_builder, digest = _source_helpers()
        criteria = criteria_builder(config)
        rubric_sha256 = digest(criteria)
    except (ImportError, KeyError, TypeError, ValueError):
        return unavailable
    judge_id = next(iter(judges))
    routes = config.get("routes")
    route = routes.get(judge_id, {}) if isinstance(routes, dict) else {}
    route = route if isinstance(route, dict) else {}
    return {"mode": "reference_and_source" if mixed else "source_judge",
            "quality_label": "Reference check pass rate" if mixed else "LLM-assessed pass rate", "judge_model_id": judge_id,
            "judge_model": route.get("model_id") or judge_id, "criteria": criteria,
            "rubric_sha256": rubric_sha256, "reference_requirement": "required" if mixed else "not_used",
            "required_check_ids": [c["id"] for c in definitions if c["required"]],
            "source_check_ids": [c["id"] for c in definitions if rules[c["id"]]["type"] == "llm_source_rubric"]}


def _core_scope(raw, config, name, group, assessment, contexts, comparison=None):
    """Verify a complete held-out reference comparison independently of optional work.

    This does not reclassify campaign failures. Unknown operations outside these
    exact planned application/grade IDs remain visible in the campaign status.
    """
    if assessment['mode'] != 'reference_and_source':
        return None
    issues, optional_unknown = [], 0
    def need(ok, message):
        if not ok and message not in issues: issues.append(message)
    def close(a, b):
        return a is None and b is None or (type(a) in (int, float) and type(b) in (int, float)
            and math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12))
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    try:
        phase='standard'
        if raw.get('mode')=='comparison_only':
            need(isinstance(comparison,dict) and comparison.get('complete') is True,'Frozen comparison provenance is unavailable or inconsistent.')
            if isinstance(comparison,dict):issues.extend(message for message in comparison.get('issues',[]) if message not in issues)
            phase=config['comparison_only']['phase']
        need(raw.get('simulated') is False, 'Live application evidence is required.')
        need(raw.get('evaluation_status') in ('complete', 'needs_review'), 'The saved run has not finished evaluation.')
        need(isinstance(raw.get('unresolved_attempts'), list), 'Campaign recovery coverage is unavailable.')
        definitions = config['document']['checks'];required = assessment['required_check_ids']
        repetitions = config['document']['execution']['repetitions']
        need(type(repetitions) is int and repetitions > 0, 'The intended repeat count is unavailable.')
        limit=config['document']['execution'].get('max_cases_per_dataset')
        need(limit is None or type(limit) is int and limit > 0, 'The frozen case cap is invalid.')
        if limit is not None and (type(limit) is not int or limit <= 0):raise ValueError('Invalid case cap')
        cases = [case for dataset in config['splits']['holdout'] for case in config['datasets'][dataset][:limit]]
        by_id = {case['id']: case for case in cases}
        need(bool(cases) and len(by_id) == len(cases), 'The intended held-out case IDs are ambiguous.')
        expected = {(case['id'], repeat) for case in cases for repeat in range(repetitions)}
        rows = group.get('trials') or [];score = group.get('score') or {}
        need(all(type(row.get('repeat')) is int and row['repeat'] >= 0 for row in rows), 'Recorded repeat indices must be nonnegative integers.')
        need(len(rows) == len(expected) and {(row.get('case_id'), row.get('repeat')) for row in rows} == expected,
             'The intended held-out cases and repeats are not complete.')
        need(score.get('n_sessions') == len(expected), 'The score denominator differs from the intended held-out sample.')
        variant = config['source'] if name == 'source_original' else config['target']
        route = config['routes'][variant['model_id']]
        prompt = score.get('prompt')
        need(isinstance(prompt, str) and bool(prompt.strip()), 'The application prompt is unavailable.')
        if name in ('source_original','target_original'):need(prompt == config['prompt'], 'The starting prompt differs from the frozen input.')
        elif name == 'target_selected':need(prompt == raw.get('selected_prompt',{}).get('prompt'), 'The selected prompt differs from the saved selection.')
        elif name == 'target_comparison':need(raw.get('mode')=='comparison_only' and prompt==config['comparison_only']['candidate_manifest']['prompt'],'The comparison prompt differs from the precommitted candidate.')
        _, _, source_digest = _source_helpers()
        from customer_eval.replay import fingerprint as request_fingerprint, request_body
        from customer_eval.transport import constrain
        from customer_eval.costs import trial_cost
        from customer_eval.timing import summarize_timing
        costs, successes, trial_ids, request_ids = [], [], [], []
        for row in rows:
            ident=row.get('id');trial_ids.append(ident);case=by_id.get(row.get('case_id'));result=row.get('result') or {};grades=row.get('checks') or {}
            need(case is not None and 'expected' in case, 'A frozen reference answer is unavailable.')
            if case is None:continue
            need(row.get('split') == 'holdout' and row.get('phase','standard') == phase and row.get('model_id') == variant['model_id'], 'An attempt belongs to a different model, phase or split.')
            need(ident == digest([config['fingerprint'],variant,prompt,case['id'],row.get('repeat'),'holdout',phase]) and result.get('trial_id') == ident,
                 'Application receipts do not bind the exact frozen model, prompt, case and repeat.')
            need(result.get('status') == 'completed' and result.get('simulated') is False, 'An application attempt did not complete with live evidence.')
            calls=result.get('route_calls') or []
            need(bool(calls) and all(c.get('status') == 'completed' and c.get('model_requested') == route['model_id'] and c.get('model_returned') == route['model_id'] for c in calls),
                 'Application model/request receipts are incomplete or inconsistent.')
            parameters=route.get('parameters') or {}
            request_ids.extend(c.get('request_id') for c in calls)
            if case.get('replay'):
                captured=case['replay']['request_sha256']
                need(request_fingerprint(case['replay']['request']) == captured and result.get('replay',{}).get('captured_request_sha256') == captured, 'The application source request does not match the frozen input.')
                need(config['document']['runner'].get('mode')=='prompt' and config['document']['runner'].get('workload')=='none' and len(calls)==1, 'The replay trace is not a single prompt-mode request.')
                effective=constrain(request_body(case['replay'],prompt,config.get('policy','')),dict(route,allowed_tool_names=[]),config['document']['runner']['limits'])
                need(result.get('replay',{}).get('effective_request_sha256')==request_fingerprint(effective), 'The effective replay request does not match the frozen prompt and settings.')
                # The request digest binds every setting. Relay summaries record
                # only a subset, so check their evidence without requiring fields
                # that the transport never recorded (e.g. temperature/top_p).
                need(all(call.get('service_tier_requested')==effective.get('service_tier','default') and
                         all(value==effective.get(key) for key,value in (call.get('effective_parameters') or {}).items()) and
                         all((call.get('effective_parameters') or {}).get(key)==effective.get(key) for key in ('reasoning','text'))
                         for call in calls), 'Recorded application settings differ from the effective request.')
                need(all((call.get('effective_parameters') or {}).get('max_output_tokens') == effective['max_output_tokens'] for call in calls), 'Recorded output budgets differ from the effective request.')
            else:
                need([turn.get('user') for turn in result.get('turn_records',[])] == case.get('turns') and bool(case.get('turns')), 'The application input history does not match the frozen case.')
                need(all(all((call.get('service_tier_requested')==(value if value is not None else 'default') if key=='service_tier' else
                              key in (call.get('effective_parameters') or {}) and call['effective_parameters'][key]==value)
                             for key,value in parameters.items()) for call in calls),
                     'Recorded application settings are missing or differ from the frozen route.')
            valid=all(isinstance(grades.get(key),dict) and type(grades[key].get('pass')) is bool and not grades[key].get('error') and grades[key].get('failure_category') not in ('grading','trace') for key in required)
            need(valid, 'A required grade is missing or has a grading/trace error.')
            successes.append(valid and all(grades[key]['pass'] for key in required))
            sources=contexts.get(case['id']);need(bool(sources), 'The frozen source context is unavailable.')
            quotes={source['source_id']:source['text'] for source in sources or []};source_sha=source_digest(sources) if sources else None
            for key in assessment['source_check_ids']:
                grade=grades.get(key)
                if result.get('status') != 'completed':
                    # Execution placeholders are not source-judge decisions.
                    # Keep the application/measurement failure above, but do
                    # not invent missing hashes or bad citations for a judge
                    # that could not assess an application answer.
                    optional_unknown += 1
                    need(not isinstance(grade,dict) or
                         (grade.get('pass') is not True and grade.get('status') not in ('pass','fail','needs_review')),
                         'A source assessment verdict contradicts the failed application.')
                    continue
                unknown=not isinstance(grade,dict) or bool(grade.get('error'))
                if unknown:optional_unknown+=1
                if not isinstance(grade,dict):continue
                for field,wanted in (('source_sha256',source_sha),('rubric_sha256',assessment['rubric_sha256'])):
                    need((unknown and field not in grade) or grade.get(field)==wanted, 'A source/rubric fingerprint is inconsistent.')
                if unknown:continue
                need(grade.get('status') in ('pass','fail','needs_review') and grade.get('pass') is (grade.get('status')=='pass'), 'A source assessment has inconsistent status.')
                citations=grade.get('evidence')
                need(isinstance(citations,list) and (grade.get('status')=='needs_review' or bool(citations)) and all(isinstance(c,dict) and c.get('source_id') in quotes and isinstance(c.get('quote'),str) and c['quote'] and c['quote'] in quotes[c['source_id']] for c in citations or []), 'Source assessment quotations do not match the frozen evidence.')
            cost=trial_cost(config,row);costs.append(cost)
            need(cost['unpriced_requests']==0, 'Application usage or pricing is incomplete.')
        need(len(set(trial_ids))==len(trial_ids), 'Application trial identities are duplicated.')
        need(all(isinstance(x,str) and x for x in request_ids) and len(set(request_ids))==len(request_ids), 'Provider request identities are missing or duplicated.')
        unresolved={x.get('id') for x in raw.get('unresolved_attempts',[]) if isinstance(x,dict)}
        need(not unresolved.intersection(set(trial_ids)|{digest(['grade',x]) for x in trial_ids}), 'A compared application or required grade remains unresolved.')
        if rows and len(rows)==len(expected):
            need(close(score.get('accuracy'),sum(successes)/len(rows)), 'The required-check score does not match recorded grades.')
            total=sum(c['known_cost_usd'] for c in costs)
            need(score.get('unpriced_requests')==0 and close(score.get('measured_model_cost_usd'),total) and close(score.get('cost_usd_per_session'),total/len(rows)) and close(score.get('cost_usd_per_success'),total/sum(successes) if sum(successes) else None), 'Application cost summaries do not match complete usage receipts.')
            scope=config['document']['runner'].get('measurement',{}).get('primary_latency','worker')
            timing=summarize_timing(rows).get(scope+'_ms',{})
            need(score.get('latency_scope')==scope and timing.get('observations')==len(rows) and close(score.get('latency_p50_s'),timing.get('p50_ms',0)/1000) and close(score.get('latency_p95_s'),timing.get('p95_ms',0)/1000), 'Application timing does not cover the intended sample or match the score.')
    except (KeyError,TypeError,ValueError,AttributeError,ZeroDivisionError):
        need(False, 'Complete frozen inputs, receipts and scoring metadata are required for this scoped comparison.')
    return {'complete':not issues,'issues':issues,'optional_assessments_unavailable':optional_unknown,
            'scope':'Held-out required reference checks and application measurements only. Campaign status and unpriced work are separate.'}


def _trial(row: dict, case: dict | None = None, *, input_bundles: dict | None = None,
           source_bundles: dict | None = None, assessment: dict | None = None, source_context=None) -> dict:
    result = row.get("result") or {}
    case = case or {}
    expected = case.get("expected")
    answer = expected.get("answer", expected.get("reference", expected)) if isinstance(expected, dict) else expected
    trial = {
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
        "expected_available": "expected" in case,
        "expected_answer": answer,
        "expected_checks": expected,
        "reference_metadata": case.get("metadata") or {},
    }
    if assessment and assessment["mode"] in ("source_judge", "reference_and_source"):
        trial.update(assessment_mode=assessment["mode"], required_check_ids=assessment.get("required_check_ids", []), source_available=False, rubric_sha256=assessment["rubric_sha256"])
        if assessment["mode"] == "source_judge":
            trial.update(expected_available=False, expected_answer=None, expected_checks=None)
        if source_context:
            _, _, digest = _source_helpers()
            source_sha256 = digest(source_context)
            reference = "source-" + source_sha256
            source_bundles.setdefault(reference, source_context)
            trial.update(source_available=True, source_ref=reference, source_sha256=source_sha256)
    # Store each exact input history once per report, rather than twice per
    # attempt and again for every repeat/model. The raw report is never changed.
    # Keep unusual/nontext legacy records inline so no information is discarded.
    inputs = trial["inputs"]
    if input_bundles is not None and inputs and all(isinstance(value, str) for value in inputs):
        encoded = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        reference = "input-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        input_bundles.setdefault(reference, inputs)
        trial["input_ref"] = reference
        del trial["inputs"]
        trial["turn_records"] = [
            {key: value for key, value in turn.items() if key != "user"}
            | {"user_input_index": index}
            for index, turn in enumerate(trial["turn_records"])
        ]
    return trial


def _usage_summary(trials: list[dict]) -> dict:
    """Require every recorded request's counter; missing usage is not zero."""
    result = {"attempts": len(trials)}
    for counter in ("input_tokens", "output_tokens", "reasoning_tokens"):
        covered, total = 0, 0
        for trial in trials:
            calls = trial.get("route_calls") or []
            values = []
            for call in calls:
                usage = call.get("usage") if isinstance(call, dict) else None
                usage = usage if isinstance(usage, dict) else {}
                source = (usage.get("output_tokens_details") or {}) if counter == "reasoning_tokens" else usage
                value = source.get(counter) if isinstance(source, dict) else None
                if type(value) is not int or value < 0:
                    break
                values.append(value)
            if calls and len(values) == len(calls):
                covered += 1
                total += sum(values)
        complete = bool(trials) and covered == len(trials)
        result[counter] = {"total": total if complete else None,
                           "mean": total / len(trials) if complete else None,
                           "covered_attempts": covered}
    return result


def _prompt_evidence(raw: dict, config: dict | None, groups: list[dict], source_bundles: dict | None = None) -> list[dict]:
    """Describe saved prompt outcomes without changing grading or selection."""
    assessment = _assessment(config)
    source_mode = assessment["mode"] in ("source_judge", "reference_and_source")
    by_id = {group["id"]: group for group in groups}
    original = by_id.get("target_original")
    evidence = []
    for after_id in ("target_selected", "target_challenger", "target_comparison"):
        revised = by_id.get(after_id)
        if original is None or revised is None:
            continue
        item = {"before_id": "target_original", "after_id": after_id,
                "available": False, "issues": [], "required_check_count": 0,
                "criteria": [], "usage": {"before": _usage_summary(original["trials"]),
                                            "after": _usage_summary(revised["trials"])},
                "outcomes": None, "example": None, "examples": [], "prompt_diff": None,
                "assessment_mode": assessment["mode"]}
        evidence.append(item)
        issues = item["issues"]
        if config is None:
            issues.append("Verified frozen evaluation criteria are unavailable.")
            continue
        if assessment["mode"] == "unverified_source_judge":
            issues.append("Source grading configuration could not be verified.")
            continue
        definitions = config.get("document", {}).get("checks")
        if (not isinstance(definitions, list) or not definitions or
                any(not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"] or
                    type(c.get("required")) is not bool for c in definitions) or
                len({c["id"] for c in definitions}) != len(definitions)):
            issues.append("The frozen check definitions are incomplete or ambiguous.")
            continue
        required = [c["id"] for c in definitions if c["required"]]
        item["required_check_count"] = len(required)
        if not required:
            issues.append("No required evaluation checks were declared.")
            continue
        scoped = assessment["mode"] == "reference_and_source" and all(g.get("core_scope", {}).get("complete") for g in (original,revised))
        item["core_scope_complete"] = scoped
        if assessment["mode"] == "reference_and_source":
            for group in (original,revised):
                issues.extend(group.get("core_scope", {}).get("issues", ["Verified core comparison metadata is unavailable."]))
        if scoped:
            item["warnings"] = ["Required reference checks and application measurements are complete. Campaign status and optional assessments are reported separately."]
        if not scoped and (raw.get("evaluation_status") != "complete" or raw.get("simulated") is not False):
            issues.append("This is not a completed live evaluation.")
        if not scoped and (not isinstance(raw.get("unresolved_attempts"), list) or raw["unresolved_attempts"]):
            issues.append("Some attempts are unresolved or their recovery coverage is unknown.")
        if not scoped and (type(raw.get("grading_error_records")) is not int or raw["grading_error_records"] != 0):
            issues.append("Grading error coverage is incomplete.")
        if original["model"] != revised["model"] or original["model"] == "Model identity unavailable":
            issues.append("The two prompts do not have the same verified model identity.")
        prompts = [g["score"].get("prompt") for g in (original, revised)]
        if any(not isinstance(p, str) or not p.strip() for p in prompts):
            issues.append("Both saved prompts are required.")
        elif prompts[0] == prompts[1]:
            issues.append("The saved prompt is unchanged; there is no prompt revision to explain.")
        else:
            item["prompt_diff"] = {"format": "unified", "text": "\n".join(difflib.unified_diff(
                prompts[0].splitlines(), prompts[1].splitlines(),
                fromfile="Original prompt", tofile="Revised prompt", lineterm="")) + "\n"}

        indexes = []
        for group in (original, revised):
            rows = group["trials"]
            keys = [(row.get("case_id"), row.get("repeat")) for row in rows]
            if (not rows or any(not isinstance(case, str) or not case or type(repeat) is not int or repeat < 0
                                for case, repeat in keys) or len(set(keys)) != len(keys) or
                    any(not isinstance(row.get("id"), str) or not row["id"] for row in rows) or
                    len({row["id"] for row in rows}) != len(rows)):
                issues.append("Unique case, repeat and trial identifiers are needed for paired examples.")
                break
            indexes.append(dict(zip(keys, rows)))
        if len(indexes) != 2 or set(indexes[0]) != set(indexes[1]):
            issues.append("The configurations do not contain matching cases and repeats.")
            continue
        pairs = [(key, indexes[0][key], indexes[1][key]) for key in sorted(indexes[0])]
        for _, before, after in pairs:
            if before["status"] != "completed" or after["status"] != "completed":
                issues.append("Some paired attempts did not complete.")
                break
            model_sets = []
            for row in (before, after):
                calls = row.get("route_calls") or []
                identities = [call.get("model_requested") if isinstance(call, dict) else None
                              for call in calls]
                model_sets.append(set(identities) if identities and
                    all(isinstance(model, str) and model.strip() for model in identities) else None)
            if model_sets[0] is None or model_sets[0] != model_sets[1]:
                issues.append("Matching model identities are unavailable for the paired attempts.")
                break
            returned_models = [{call["model_returned"] for call in row["route_calls"]
                                if isinstance(call.get("model_returned"), str) and call["model_returned"].strip()}
                               for row in (before, after)]
            if all(returned_models) and returned_models[0] != returned_models[1]:
                issues.append("The paired attempts recorded different returned model identities.")
                break
            left = before.get("input_ref", before.get("inputs"))
            right = after.get("input_ref", after.get("inputs"))
            if (not left or not right or left != right or
                    isinstance(left, list) and any(value is None for value in left)):
                issues.append("Matching input histories are unavailable for the paired attempts.")
                break
            if source_mode:
                if (not before.get("source_available") or not after.get("source_available") or
                        before.get("source_ref") != after.get("source_ref") or
                        before.get("rubric_sha256") != after.get("rubric_sha256")):
                    issues.append("Matching verified source text and rubric are unavailable.")
                    break
                source_checks = [c["id"] for c in definitions if config.get("grading", {}).get(c["id"], {}).get("type") == "llm_source_rubric"]
                for row in (before, after):
                    sources = {source["source_id"]: source["text"] for source in (source_bundles or {}).get(row.get("source_ref"), [])}
                    for ident in source_checks:
                        check = (row.get("checks") if isinstance(row.get("checks"), dict) else {}).get(ident)
                        if scoped and ident not in required and (not isinstance(check, dict) or check.get("error")):
                            continue  # The verified core scope checks any supplied source/rubric fingerprints.
                        if (not isinstance(check, dict) or check.get("error") or check.get("status") not in ("pass", "fail", "needs_review") or
                                check.get("pass") is not (check["status"] == "pass") or
                                check.get("source_sha256") != row["source_sha256"] or
                                check.get("rubric_sha256") != assessment["rubric_sha256"]):
                            issues.append("Source judgment, source digest or rubric digest is invalid: " + ident + ".")
                            continue
                        citations = check.get("evidence")
                        if (not isinstance(check.get("reason"), str) or not check["reason"].strip() or
                                not isinstance(citations, list) or (check["status"] != "needs_review" and not citations) or
                                any(not isinstance(citation, dict) or not isinstance(citation.get("source_id"), str) or
                                    citation["source_id"] not in sources or not isinstance(citation.get("quote"), str) or
                                    not citation["quote"].strip() or citation["quote"] not in sources[citation["source_id"]] for citation in citations)):
                            issues.append("Source judgment evidence does not match the frozen sources: " + ident + ".")
            if assessment["mode"] != "source_judge" and (not before.get("expected_available") or not after.get("expected_available") or
                    before.get("expected_answer") != after.get("expected_answer")):
                issues.append("Matching frozen reference answers are unavailable.")
                break
        grading = config.get("grading") or {}
        grading = grading if isinstance(grading, dict) else {}
        for definition in definitions:
            ident = definition["id"]
            rule = grading.get(ident) or {}
            rule = rule if isinstance(rule, dict) else {}
            criterion = {"id": ident, "label": ident.replace("_", " ").capitalize(),
                         "question": definition.get("question") or ident,
                         "required": definition["required"], "type": rule.get("type"),
                         "path": rule.get("path"), "failure_change": None}
            for side, group in (("before", original), ("after", revised)):
                results = [(row["checks"] if isinstance(row["checks"], dict) else {}).get(ident)
                           for row in group["trials"]]
                passed = sum(isinstance(c, dict) and c.get("pass") is True and not c.get("error") for c in results)
                needs_review = sum(source_mode and isinstance(c, dict) and c.get("status") == "needs_review" and c.get("pass") is False and not c.get("error") for c in results)
                failed = sum(isinstance(c, dict) and c.get("pass") is False and not c.get("error") and
                             not (source_mode and c.get("status") == "needs_review") for c in results)
                criterion[side] = {"passed": passed, "failed": failed,
                                   "unknown": len(results) - passed - failed - needs_review, "total": len(results)}
                if source_mode:
                    criterion[side]["needs_review"] = needs_review
            if not criterion["before"]["unknown"] and not criterion["after"]["unknown"]:
                criterion["failure_change"] = criterion["before"]["failed"] - criterion["after"]["failed"]
            elif definition["required"]:
                issues.append("Required-check results are missing or contain grading errors: " + ident + ".")
            item["criteria"].append(criterion)
        item["criteria"].sort(key=lambda c: (not c["required"],
            -(c["failure_change"] if c["failure_change"] is not None else -10**9), c["id"]))
        if issues:
            continue
        def all_passed(row):
            return all(row["checks"][ident]["pass"] is True for ident in required)
        outcomes = {"attempts": len(pairs), "before_passes": 0, "after_passes": 0,
                    "improved": 0, "regressed": 0, "both_passed": 0, "both_failed": 0}
        if source_mode:
            outcomes.update(before_needs_review=0, after_needs_review=0)
        for (case_id, repeat), before, after in pairs:
            a, b = all_passed(before), all_passed(after)
            outcome = "both_passed" if a and b else "both_failed" if not a and not b else "improved" if b else "regressed"
            outcomes["before_passes"] += a
            outcomes["after_passes"] += b
            outcomes[outcome] += 1
            if source_mode:
                for side, row in (("before", before), ("after", after)):
                    outcomes[side + "_needs_review"] += any(row["checks"][ident].get("status") == "needs_review" for ident in required)
            item["examples"].append({"case_id": case_id, "repeat": repeat,
                "before_trial_id": before["id"], "after_trial_id": after["id"], "outcome": outcome})
        for side, group in (("before", original), ("after", revised)):
            score = group["score"]
            accuracy = score.get("accuracy")
            if (type(score.get("n_sessions")) is not int or score["n_sessions"] != len(pairs) or
                    type(accuracy) not in (float, int) or not math.isfinite(accuracy) or
                    not math.isclose(accuracy, outcomes[side + "_passes"] / len(pairs), abs_tol=1e-12)):
                issues.append("Saved aggregate scores do not reconcile to the required checks.")
        if issues:
            item["examples"] = []
            continue
        item["outcomes"] = outcomes
        for criterion in item["criteria"]:
            if not criterion["required"] or (criterion["failure_change"] or 0) <= 0:
                continue
            ident = criterion["id"]
            match = next((pair for pair in item["examples"] if pair["outcome"] == "improved" and
                indexes[0][(pair["case_id"], pair["repeat"])]["checks"][ident]["pass"] is False and
                (not source_mode or indexes[0][(pair["case_id"], pair["repeat"])]["checks"][ident].get("status") != "needs_review")), None)
            if match:
                item["example"] = dict(match, focus_check_id=ident,
                    selection_reason="Chosen to illustrate the required check with the largest reduction in failures. "
                    "This original answer failed that check; the revised answer passed every required check. "
                    "This example does not establish which prompt edit caused the change.")
                break
        if item["example"] is None:
            for outcome in ("regressed", "both_failed", "both_passed", "improved"):
                match = next((pair for pair in item["examples"] if pair["outcome"] == outcome), None)
                if match:
                    item["example"] = dict(match, focus_check_id=None,
                        selection_reason="First paired example in the " + outcome.replace("_", " ") +
                        " group, sorted by case and repeat. Inspect other pairs using the selector; this is not a representative sample.")
                    break
        item["available"] = True
    return evidence


def _model_roles(raw: dict) -> list[dict]:
    """Describe observed optimizer/judge calls separately from application rows."""
    roles = []
    for role, key in (("optimizer", "optimizer_requests"), ("judge", "judge_requests")):
        requests = raw.get(key)
        if not isinstance(requests, list) or not requests:
            continue
        models, efforts = set(), set()
        for request in requests:
            if not isinstance(request, dict):
                continue
            model = request.get("model_requested") or request.get("model_returned")
            if isinstance(model, str) and model:
                models.add(model)
            params = request.get("effective_parameters") or {}
            reasoning = params.get("reasoning") if isinstance(params, dict) else None
            effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
            if isinstance(effort, str) and effort:
                efforts.add(effort)
        roles.append({"role": role, "model": ", ".join(sorted(models)) or "Model identity unavailable",
                      "reasoning": ", ".join(sorted(efforts)) or None, "requests": len(requests)})
    return roles


def _report(path: Path, index: int) -> dict:
    raw = read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("holdout"), dict):
        raise ValueError(f"{path.name} is not an evaluation report with a holdout section.")
    if not isinstance(raw.get("simulated"), bool):
        raise ValueError("A saved report must declare whether its measurements are simulated.")
    backend = str(raw.get("backend") or "unknown")
    config, frozen_status = _frozen_input(path, raw)
    comparison=None
    if raw.get('mode')=='comparison_only':
        spec=importlib.util.spec_from_file_location('saved_comparison_evidence',Path(__file__).with_name('comparison_evidence.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        comparison=module.inspect_comparison(path.parent,raw,config)
    cases = {case["id"]: case for rows in (config or {}).get("datasets", {}).values()
             for case in rows if isinstance(case, dict) and "id" in case}
    cost_metric, cost_metric_source = _cost_metric(raw, config)
    assessment = _assessment(config)
    groups = []
    input_bundles = {}
    source_bundles, contexts = {}, {}
    if assessment["mode"] in ("source_judge", "reference_and_source"):
        context_builder, _, _ = _source_helpers()
        for ident, case in cases.items():
            try:
                contexts[ident] = context_builder(case, policy=config.get("policy", ""),
                    general_rules=(config.get("intake_context") or {}).get("quality", {}).get("rules", ""))
            except (KeyError, TypeError, ValueError):
                contexts[ident] = None
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
            "core_scope": _core_scope(raw, config, name, group, assessment, contexts, comparison) if config is not None else None,
            "cost_metrics": _cost_values(group.get("score") or {}),
            "trials": [_trial(row, cases.get(row.get("case_id")), input_bundles=input_bundles,
                              source_bundles=source_bundles, assessment=assessment,
                              source_context=contexts.get(row.get("case_id"))) for row in rows],
        })
    historical = _is_bundled_recording(path, backend)
    return {
        "id": f"report-{index}",
        "mode": raw.get('mode'),
        "comparison_context": comparison,
        "backend": backend,
        "fingerprint": raw.get("fingerprint"),
        "simulated": raw["simulated"],
        "status": raw.get("evaluation_status", "unknown"),
        "source_name": f"{path.parent.name}/{path.name}",
        "historical": historical,
        "provenance": _provenance(path, raw, historical, config, frozen_status),
        "dataset_summary": _dataset_summary(config, historical),
        "frozen_input_status": frozen_status,
        "cost_metric": cost_metric,
        "cost_metric_source": cost_metric_source,
        "model_costs": raw.get("model_costs") or {},
        "assessment": assessment,
        "model_roles": _model_roles(raw),
        # An absent ledger means unknown coverage, not zero unresolved attempts.
        "unresolved_attempts": raw.get("unresolved_attempts"),
        "grading_error_records": raw.get("grading_error_records"),
        "cost_note": raw.get("cost_note") or "Missing usage or pricing remains unknown. Model costs exclude infrastructure and human review.",
        "latency_scope": raw.get("latency_scope") or "Consult the saved timing scope before comparing latency.",
        "claim": raw.get("claim") or "Results apply only to the evaluated cases, settings and routes.",
        "selected_prompt": raw.get("selected_prompt") or {},
        "optimization_recorded": isinstance(raw.get("optimization"), dict),
        "reused_groups": {name: group["reused_from"] for name, group in raw["holdout"].items()
                          if isinstance(group, dict) and group.get("reused_from")},
        "groups": groups,
        "prompt_evidence": _prompt_evidence(raw, config, groups, source_bundles),
        "input_storage": "content-addressed-v1",
        "input_bundles": input_bundles,
        "source_bundles": source_bundles,
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
    cost_metrics = {report["cost_metric"] for report in reports}
    return {
        "title": title or ("Recorded model comparison" if historical else "Model comparison"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical": historical,
        "provenance": " ".join(provenance),
        "default_cost_metric": next(iter(cost_metrics)) if len(cost_metrics) == 1 else None,
        "cost_metric_conflict": len(cost_metrics) > 1,
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
    if template.count(FONT_PLACEHOLDER) != 1:
        raise ValueError("Dashboard template must contain exactly one font placeholder.")
    template = template.replace(FONT_PLACEHOLDER, embedded_fonts())
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
