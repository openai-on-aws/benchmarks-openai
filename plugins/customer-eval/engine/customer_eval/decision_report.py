"""Decision summary from immutable eval evidence; never manufacture a winner."""
from collections import Counter
from difflib import unified_diff
from html import escape
import json
from pathlib import Path

from .input_preview import html_document

LABELS = {'source_original': 'Current model with original prompt',
          'target_original': 'New model with original prompt',
          'target_selected': 'New model with selected prompt',
          'target_challenger': 'New model with candidate for review'}


def _review_guidance(candidates, report, cost_metric, cost_label, required):
    """Describe frozen validation decisions and any separately measured challenger."""
    notes = ['The original prompt remains selected while candidate tradeoffs await customer review.']
    cost_key = 'model_usd_per_success' if cost_metric == 'cost_usd_per_success' else 'model_usd_per_trial'
    if len(candidates) > 1:
        generation = report['optimization']['generations'][-1]['index']
        comparison = next((c for c in report.get('joint_comparisons', []) if c['generation'] == generation), {})
        scores = comparison.get('scores', [])
        baseline = scores[0] if scores else {}
        if (baseline.get('prompt') == report['selected_prompt']['prompt'] and
                baseline.get(cost_metric) is not None and baseline.get('latency_p95_s') is not None and
                all(c[cost_key] is not None and c['p95_seconds'] is not None and
                    c['quality'] >= baseline['accuracy'] and c[cost_key] <= baseline[cost_metric] and
                    c['p95_seconds'] <= baseline['latency_p95_s'] and
                    (c['quality'] > baseline['accuracy'] or c[cost_key] < baseline[cost_metric] or
                     c['p95_seconds'] < baseline['latency_p95_s']) for c in candidates)):
            notes.append('Each candidate awaiting review improves on the original prompt in matched validation without worsening '
                         'required-check pass rate, ' + cost_label + ' or observed p95. The unresolved tradeoff is between these improved candidates.')
            notes.append(f"For the matched validation original, {baseline['accuracy']:.1%} of attempts passed all required checks, with " +
                         cost_label + f" ${baseline[cost_metric]:.6f} and observed p95 {baseline['latency_p95_s']:.3f} s.")
        for key, label, unit, digits in [(cost_key, cost_label, '$', 6), ('p95_seconds', 'observed p95', '', 3)]:
            if any(c[key] is None for c in candidates):
                notes.append('Final validation ' + label + ' is not fully measured; no ranking is available.')
                continue
            lowest = min(c[key] for c in candidates)
            names = ', '.join(c['id'].replace('-', ' ') for c in candidates if c[key] == lowest)
            value = unit + f'{lowest:.{digits}f}' + (' s' if key == 'p95_seconds' else '')
            notes.append('Final matched validation: ' + names + ' has the lowest ' + label + ' (' + value + ').')
        notes.append('Review correctness first, then decide whether lower cost or lower observed p95 matters more for this workload. No weighting is applied automatically.')
    else:
        notes.append('Review ' + candidates[0]['id'].replace('-', ' ') + ' against the validation incumbent and decide whether its quality, cost and latency tradeoff is acceptable.')
    challenger = (report['holdout'].get('target_challenger') or {}).get('score')
    matched = next((c for c in candidates if challenger and c['prompt'] == challenger.get('prompt')), None)
    if matched:
        name = matched['id'].replace('-', ' ')
        trials = report['holdout']['target_challenger']['trials']
        notes.append(name.capitalize() + ' passed ' + str(sum(
            r['result']['status'] == 'completed' and all(r['checks'][key]['pass'] for key in required)
            for r in trials)) + '/' + str(len(trials)) + ' held-out attempts.')
        original = report['holdout']['target_original']['score']
        for key, label, digits, unit in [(cost_metric, cost_label, 6, '$'), ('latency_p95_s', 'observed p95', 3, '')]:
            before, after = original.get(key), challenger.get(key)
            if before is None or after is None:
                notes.append('Held-out ' + label + ' cannot be compared with the new model’s original prompt because at least one value is unknown.')
            else:
                relation = 'lower than' if after < before else 'higher than' if after > before else 'the same as'
                notes.append('Its held-out ' + label + ' is ' + relation + ' the new model’s original prompt (' +
                             unit + f'{after:.{digits}f}' + ' versus ' + unit + f'{before:.{digits}f}' +
                             (' s' if key == 'latency_p95_s' else '') + ').')
        if challenger['accuracy'] == 0:
            notes.append('The held-out candidate passed no attempts; address its failing cases before considering a customer pilot.')
        elif challenger['accuracy'] < report['holdout']['source_original']['score']['accuracy']:
            notes.append('The held-out candidate trails the current model on required-check pass rate; review its failing cases before considering a customer pilot.')
        if challenger.get('eligible') is False:
            notes.append('The held-out candidate did not meet the configured absolute thresholds; review those failures before considering a customer pilot.')
        if len(candidates) > 1:
            notes.append('The other candidates awaiting review have no held-out results in this run.')
    else:
        notes.append('No held-out results are available for the candidates awaiting review.')
    notes.append('Held-out evidence informs this review; it does not promote a candidate or change the frozen prompt selection.')
    return notes


def build(config, report):
    selection_policy = report['selected_prompt'].get('policy', config['document']['runner'].get('selection', {})) or {}
    cost_metric = selection_policy.get('cost_metric', 'cost_usd_per_session')
    cost_label = 'cost per success' if cost_metric == 'cost_usd_per_success' else 'cost per attempt'
    cost_explanation = ('For each configuration and split, cost per attempt is total measured model spend across all '
                        'evaluated attempts divided by the number of attempts. Cost per success uses the same spend, '
                        'including attempts that failed required checks, divided by attempts passing all required checks. '
                        'Each repeat is an attempt. Missing usage or pricing remains unknown; cost per success is unknown '
                        'with zero successful attempts. Campaign spend, including optimizer and judge calls, is reported separately.')
    all_cases = [c for rows in config['datasets'].values() for c in rows]
    recorded = any(c.get('metadata', {}).get('reference_kind') == 'recorded_response' for c in all_cases)
    unreviewed = any(d.get('label_status') != 'reviewed' for d in config['document']['datasets'])
    metric = ('Agreement with recorded outputs' if recorded else
              'Reference checks passed (labels unreviewed)' if unreviewed else 'Required quality checks passed')
    required = [c['id'] for c in config['document']['checks'] if c['required']]
    rows, failures = [], []
    for name, group in report['holdout'].items():
        if group.get('reused_from'):
            continue
        score = group['score']
        trials = group['trials']
        variant = config['source'] if name.startswith('source') else config['target']
        model = config['routes'][variant['model_id']]['model_id'] or 'Unresolved (simulation)'
        counts = Counter()
        for row in trials:
            for check_id, check in row['checks'].items():
                if not check['pass']:
                    category = ('execution' if row['result']['status'] != 'completed' else
                                'grading' if check.get('error') else check.get('failure_category') or 'behavior')
                    counts[category] += 1
                    if len(failures) < 24:
                        failures.append({'configuration': name, 'case_id': row['case_id'], 'repeat': row['repeat'],
                            'check': check_id, 'category': category, 'reason': check['reason'],
                            'differences': check.get('differences', []),
                            'actual_excerpt': str(row['result'].get('outputs', []))[:900]})
        passed = sum(r['result']['status'] == 'completed' and all(r['checks'][c]['pass'] for c in required) for r in trials)
        rows.append({'configuration': name, 'model': model, 'distinct_cases': len({r['case_id'] for r in trials}),
            'passed_trials': passed, 'planned_trials': len(trials), 'check_pass_rate': score['accuracy'],
            'model_usd_per_trial': score['cost_usd_per_session'], 'model_usd_per_success': score.get('cost_usd_per_success'),
            'median_seconds': score['latency_p50_s'], 'p95_seconds': score['latency_p95_s'],
            'failures_by_category': dict(counts), 'timing': score.get('timing_metrics')})
    source = report['holdout']['source_original']['score']
    target = report['holdout']['target_original']['score']
    selected = report['holdout']['target_selected']['score']
    candidates, final_candidates = [], []
    for generation in (report.get('optimization') or {}).get('generations', []):
        final_candidates = []
        for index, (candidate, decision) in enumerate(zip(generation['candidates'], generation['decisions']), 1):
            item = {'id': f"generation-{generation['index']}-candidate-{index}",
                'prompt': candidate['prompt'], 'quality': candidate['accuracy'],
                'model_usd_per_trial': candidate['cost_usd_per_session'],
                'model_usd_per_success': candidate.get('cost_usd_per_success'), 'p95_seconds': candidate['latency_p95_s'],
                'decision': decision, 'comparison': 'matched validation; not holdout'}
            candidates.append(item)
            final_candidates.append(item)
    changed = report['selected_prompt']['prompt'] != config['prompt']
    review_candidates = [c for c in final_candidates if not changed and c['decision'].get('accepted')
                         and c['decision'].get('needs_review') and not c['decision'].get('selected')
                         and c['prompt'] != config['prompt']]
    recommendation = 'Review these results and references before a customer pilot.'
    reasons = []
    if report['simulated']:
        recommendation = 'Run a live comparison; this simulation only checks the workflow.'
    elif report['evaluation_status'] != 'complete':
        recommendation = 'Resolve incomplete attempts or grader errors before comparing models.'
    elif review_candidates:
        recommendation = 'Review candidate prompt tradeoffs and the held-out evidence before choosing a revision; the original prompt remains selected.'
        challenger = (report['holdout'].get('target_challenger') or {}).get('score', {})
        if challenger.get('accuracy') == 0 and any(c['prompt'] == challenger.get('prompt') for c in review_candidates):
            recommendation = 'Review candidate prompt tradeoffs, but address the held-out candidate’s failing cases before considering a customer pilot.'
        if all(c['quality'] == 0 for c in review_candidates):
            recommendation = 'Address the candidates’ failing cases before considering a customer pilot; no candidate awaiting review passed any validation attempts.'
        reasons.extend(_review_guidance(review_candidates, report, cost_metric, cost_label, required))
        if selected['accuracy'] == 0:
            reasons.append('The retained original prompt passed no held-out attempts and is not ready for a customer pilot.')
        elif selected['accuracy'] < source['accuracy']:
            reasons.append('The new model with the retained original prompt trails the current model on held-out required-check pass rate. Review candidate revisions separately.')
    elif selected['accuracy'] == 0:
        recommendation = 'Address the failing cases before considering this configuration for a customer pilot.'
    elif selected['accuracy'] < source['accuracy']:
        recommendation = 'Keep the current model pending review of the target model’s failing cases.'
    else:
        pairs = [('quality', source['accuracy'], selected['accuracy'], 1),
                 (cost_label, source.get(cost_metric), selected.get(cost_metric), -1),
                 ('p95 time', source['latency_p95_s'], selected['latency_p95_s'], -1)]
        for label, before, after, direction in pairs:
            if before is None or after is None:
                reasons.append(label + ' is not fully measured')
            elif after != before:
                reasons.append(label + (' improved' if (after-before)*direction > 0 else ' worsened'))
        if any('worsened' in s or 'not fully' in s for s in reasons):
            recommendation = 'Review the quality, cost and latency tradeoff before changing the application.'
        else:
            recommendation = 'The selected configuration is a candidate for a customer pilot after reference and failure review.'
    if recorded:
        reasons.append('Matching previous model responses does not establish correctness.')
    if unreviewed:
        reasons.append('Expected answers have not been independently reviewed; this is not production accuracy.')
    prompt_explanation = ('A revised prompt was selected.'
                          if changed else 'The original prompt was retained. Candidate results and tradeoffs are listed below.'
                          if candidates else 'No prompt revision was evaluated.')
    if changed and any(c['prompt'] == report['selected_prompt']['prompt'] and
                       c['decision'].get('selected') and
                       c['decision'].get('selection_rule') == 'accuracy_then_cost_then_latency_v1'
                       for c in candidates):
        prompt_explanation += (' Eligible improvements were ranked by highest required-check pass rate, '
                               f'then lowest {cost_label}, then lowest p95 latency.')
    if review_candidates:
        prompt_explanation = ('The original prompt was retained for customer review of candidate tradeoffs. '
                              'Candidates awaiting review met the recorded validation selection requirements; they were not automatically selected.')
    elif not changed and final_candidates and all(c['decision'].get('accepted') is False for c in final_candidates):
        prompt_explanation = ('The original prompt was retained. No candidate in the final validation generation met the selection requirements; '
                              'review the recorded rejection reasons.')
    if candidates or changed:
        prompt_explanation += (' Prompt selection used validation results and was frozen before the held-out evaluation. '
                               'Held-out results do not select or replace the prompt; they inform the recommendation for a customer pilot.')
    return {'recommendation': recommendation, 'reasons': reasons, 'quality_label': metric,
        'selection_cost_metric': cost_metric, 'selection_cost_label': cost_label,
        'cost_denominators': cost_explanation,
        'recommendation_scope': ('The recommendation addresses candidates awaiting a customer choice after validation. '
                                 'Held-out challenger evidence informs that review without changing the frozen selection.' if review_candidates else
                                'The recommendation compares the current model with the new model and selected prompt '
                                 'on held-out cases, using ' + cost_label + ' for the cost comparison.'),
        'model_comparison': {'source_original': source, 'target_original': target,
                             'scope': 'Same original prompt and held-out cases; only the model route changes.'},
        'prompt_comparison': {'changed': changed, 'status': 'selected_revision' if changed else 'original_retained',
            'review_required': bool(review_candidates), 'review_candidate_ids': [c['id'] for c in review_candidates],
            'explanation': prompt_explanation,
            'reason': report['selected_prompt']['reason'], 'target_original': target, 'target_selected': selected,
            'candidates': candidates, 'scope': 'Same target model and held-out cases; only the editable prompt changes. Candidate selection used validation.'},
        'holdout': rows, 'failure_examples': failures,
        'input_context': config.get('intake_context'),
        'models': {k: {'model': r['model_id'], 'parameters': r['parameters'], 'endpoint': r['base_url'],
                      'pricing_source': r.get('pricing_source'), 'pricing_verified_at': r.get('pricing_verified_at')}
                   for k, r in config['routes'].items()},
        'model_costs': report['model_costs'], 'cost_scope': report['cost_note'],
        'latency_scope': report['latency_scope'],
        'limitations': ['Repeated trials are not additional independent customer cases.',
            'Reported p95 is descriptive, not proof of a statistically reliable speed difference.',
            'Excluded records are outside the quality denominator and remain visible in the input preview.',
            'Text replay measures one new answer against recorded history; it does not test the upstream PDF/OCR stage.',
            'Provider request time includes network and provider processing; it cannot isolate model compute from Bedrock.'],
        'fingerprint': report['fingerprint'], 'backend': report['backend']}


def write(config, report, directory, qualification=None):
    directory = Path(directory)
    decision = build(config, report)
    if qualification:
        costs = [m['model_cost']['estimated_cost_usd'] for m in qualification['models']]
        cost = sum(costs) if all(x is not None for x in costs) else None
        run_cost = report['model_costs']['estimated_model_cost_usd']
        decision['qualification_model_cost_usd'] = cost
        decision['campaign_plus_qualification_model_cost_usd'] = cost + run_cost if cost is not None and run_cost is not None else None
    (directory / 'decision.json').write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    (directory / 'original-prompt.txt').write_text(config['prompt'])
    (directory / 'selected-prompt.txt').write_text(report['selected_prompt']['prompt'])
    diff = '\n'.join(unified_diff(config['prompt'].splitlines(), report['selected_prompt']['prompt'].splitlines(),
                                  fromfile='original', tofile='selected', lineterm=''))
    (directory / 'prompt.diff').write_text(diff or 'The original prompt was retained.\n')
    prompt_dir = directory / 'candidate-prompts'
    prompt_dir.mkdir(exist_ok=True)
    for item in decision['prompt_comparison']['candidates']:
        (prompt_dir / (item['id'] + '.txt')).write_text(item['prompt'])
    def number(v, digits=3):
        return 'unknown' if v is None else f'{v:.{digits}f}'
    lines = ['# Model and prompt comparison', '', decision['recommendation'], '']
    lines += ['- ' + x for x in decision['reasons']]
    cost_selection = 'Configured cost metric: ' + decision['selection_cost_label'] + ' (' + decision['selection_cost_metric'] + ').'
    lines += ['', cost_selection, '', decision['recommendation_scope'], '', decision['cost_denominators']]
    lines += ['', '## Final held-out results', '', 'Quality measure: ' + decision['quality_label'] + '.',
              '', '| Configuration | Model | Passed / attempts | Distinct cases | Model USD/attempt | Model USD/success | Median s | p95 s |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    table = '<table><tr>' + ''.join('<th>' + x + '</th>' for x in
        ('Configuration', 'Model', 'Passed / attempts', 'Distinct cases', 'Model USD/attempt', 'Model USD/success', 'Median s', 'p95 s')) + '</tr>'
    for r in decision['holdout']:
        cells = [LABELS.get(r['configuration'], r['configuration']), r['model'], f"{r['passed_trials']} / {r['planned_trials']}",
                str(r['distinct_cases']), number(r['model_usd_per_trial'], 6), number(r['model_usd_per_success'], 6),
                number(r['median_seconds']), number(r['p95_seconds'])]
        lines.append('| ' + ' | '.join(cells) + ' |')
        table += '<tr>' + ''.join('<td>' + escape(c) + '</td>' for c in cells) + '</tr>'
    table += '</table>'
    lines += ['', 'Model comparison: ' + decision['model_comparison']['scope'],
              '', 'Prompt comparison: ' + decision['prompt_comparison']['scope'],
              '', decision['prompt_comparison']['explanation'],
              '', 'The full original, selected and candidate prompts are included beside this report.',
              '', '## Cost and timing', '', report['cost_note'], '', report['latency_scope'],
              '', 'Campaign model cost: $' + number(report['model_costs']['estimated_model_cost_usd'], 6) + '.']
    lines += ['', '## Candidate results on validation', '',
              '| Candidate | Required checks pass | Model USD/attempt | Model USD/success | p95 s | Outcome |', '|---|---:|---:|---:|---:|---|']
    candidate_table = '<table><tr>' + ''.join('<th>' + x + '</th>' for x in
        ('Candidate', 'Required checks pass', 'Model USD/attempt', 'Model USD/success', 'p95 s', 'Outcome')) + '</tr>'
    for c in decision['prompt_comparison']['candidates']:
        outcome = ('Selected' if c['decision'].get('selected') else 'Tradeoff for review' if c['decision'].get('needs_review') else 'Not selected')
        reasons = c['decision'].get('reasons', []) + c['decision'].get('notes', [])
        cells = [c['id'].replace('-', ' '), f"{c['quality']:.1%}", number(c['model_usd_per_trial'], 6),
                 number(c['model_usd_per_success'], 6), number(c['p95_seconds']),
                 outcome + (': ' + '; '.join(reasons) if reasons else '')]
        lines += ['| ' + ' | '.join(cells) + ' |']
        candidate_table += '<tr>' + ''.join('<td>' + escape(c) + '</td>' for c in cells) + '</tr>'
    candidate_table += '</table>'
    if qualification:
        lines += ['Campaign plus qualification model cost: $' + number(decision['campaign_plus_qualification_model_cost_usd'], 6) + '.']
    lines += ['', '## Failed examples', '']
    lines += ['- ' + f"{r['configuration']} / {r['case_id']} / {r['check']}: {r['reason']}" for r in decision['failure_examples']]
    if not decision['failure_examples']:
        lines += ['No failed required checks were observed in the final held-out sample.']
    lines += ['', '## Limits of this result', ''] + ['- ' + x for x in decision['limitations']]
    (directory / 'decision.md').write_text('\n'.join(lines) + '\n')
    body = '<p class="note">' + escape(decision['recommendation']) + '</p><ul>' + ''.join('<li>' + escape(x) + '</li>' for x in decision['reasons']) + '</ul>'
    body += '<p>' + escape(cost_selection) + '</p><p>' + escape(decision['recommendation_scope']) + '</p><p>' + escape(decision['cost_denominators']) + '</p>'
    body += '<h2>Final held-out results</h2><p>' + escape(decision['quality_label']) + '</p>' + table
    body += '<p>' + escape(decision['model_comparison']['scope']) + '</p><p>' + escape(decision['prompt_comparison']['scope']) + '</p>'
    body += '<h2>Prompt selection</h2><p>' + escape(decision['prompt_comparison']['explanation']) + '</p>'
    for title, value in [('Original prompt', config['prompt']), ('Selected prompt', report['selected_prompt']['prompt']),
                         ('Changes', diff or 'Original retained; no change selected.')]:
        body += '<details><summary>' + title + '</summary><pre>' + escape(value) + '</pre></details>'
    body += '<h2>Candidate results on validation</h2>' + candidate_table
    for c in decision['prompt_comparison']['candidates']:
        outcome = ('Selected' if c['decision'].get('selected') else 'Tradeoff for review' if c['decision'].get('needs_review') else 'Not selected')
        facts = (f"{c['quality']:.1%} passed required checks; model cost $" + number(c['model_usd_per_trial'], 6) +
                 ' per attempt and $' + number(c['model_usd_per_success'], 6) + ' per success; p95 ' +
                 number(c['p95_seconds']) + ' seconds on matched validation cases.')
        reasons = c['decision'].get('reasons', []) + c['decision'].get('notes', [])
        body += '<details><summary>' + escape(c['id'].replace('-', ' ').capitalize() + ' — ' + outcome) + '</summary><p>' + escape(facts) + '</p><p>' + escape('; '.join(reasons)) + '</p><pre>' + escape(c['prompt']) + '</pre></details>'
    body += '<h2>Cost and timing</h2><pre>' + escape(json.dumps({k: v for k, v in decision.items() if k in
        ('model_costs', 'qualification_model_cost_usd', 'campaign_plus_qualification_model_cost_usd')}, indent=2)) + '</pre><p>' + escape(report['cost_note']) + '</p><p>' + escape(report['latency_scope']) + '</p>'
    body += '<h2>Failed examples</h2>'
    for failure in decision['failure_examples']:
        body += '<details><summary>' + escape(failure['case_id'] + ' / ' + failure['check']) + '</summary><pre>' + escape(json.dumps(failure, indent=2, ensure_ascii=False)) + '</pre></details>'
    body += '<h2>Limits of this result</h2><ul>' + ''.join('<li>' + escape(x) + '</li>' for x in decision['limitations']) + '</ul>'
    (directory / 'decision.html').write_text(html_document('Model and prompt comparison', body))
    return decision
