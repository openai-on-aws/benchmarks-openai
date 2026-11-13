"""Rank eligible improvements by accuracy, configured cost, then p95 latency."""
import math

SELECTION_RULE = 'accuracy_then_cost_then_latency_v1'


def validate_policy(policy):
    if policy['mode'] != 'joint_cost_latency':
        raise ValueError('Unsupported selection policy')
    if policy.get('cost_metric', 'cost_usd_per_session') not in ('cost_usd_per_session', 'cost_usd_per_success'):
        raise ValueError('Unsupported selection cost_metric')
    guarded = policy.get('non_regression_checks', [])
    if (not isinstance(guarded, list) or any(not isinstance(k, str) or not k for k in guarded)
            or len(guarded) != len(set(guarded))):
        raise ValueError('Invalid non_regression_checks')
    for key in ('min_cost_improvement_fraction', 'min_latency_improvement_fraction'):
        if key not in policy:
            continue
        value = policy[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 1:
            raise ValueError('Joint improvement thresholds must be finite fractions between zero and one')
    if type(policy['min_validation_sessions']) is not int or policy['min_validation_sessions'] < 20:
        raise ValueError('Joint selection requires at least 20 validation sessions')


def choose_joint(baseline, candidates, policy):
    validate_policy(policy)
    cost_metric = policy.get('cost_metric', 'cost_usd_per_session')
    decisions, accepted = [], []
    for candidate in candidates:
        reasons = list(candidate.rejection_reasons)
        if not candidate.eligible:
            reasons.append('required gates failed')
        if candidate.accuracy < baseline.accuracy:
            reasons.append('accuracy regressed')
        for key in policy.get('non_regression_checks', []):
            before = baseline.criterion_failure_counts.get(key)
            after = candidate.criterion_failure_counts.get(key)
            if type(before) is not int or type(after) is not int or before < 0 or after < 0 or candidate.n_sessions != baseline.n_sessions:
                reasons.append(key + ' failure counts unavailable or unmatched')
            elif after > before:
                reasons.append(key + ' failures increased')
        if candidate.latency_scope != baseline.latency_scope:
            reasons.append('latency scopes differ')
        if min(candidate.n_sessions, baseline.n_sessions) < policy['min_validation_sessions']:
            reasons.append('insufficient validation sessions')
        improvements = {}
        for key, attribute in [('cost', cost_metric), ('latency', 'latency_p95_s')]:
            before, after = getattr(baseline, attribute), getattr(candidate, attribute)
            if (before is None or after is None or not math.isfinite(before) or not math.isfinite(after)
                    or before <= 0 or after < 0):
                reasons.append(f'{key} measurement unavailable')
                improvements[key] = None
            else:
                gain = (before - after) / before
                improvements[key] = gain
                threshold = policy.get(f'min_{key}_improvement_fraction')
                if threshold is not None and gain + 1e-12 < threshold:
                    reasons.append(f'{key} improvement below threshold')
        decisions.append({'prompt': candidate.prompt, 'accepted': not reasons,
                          'reasons': reasons, 'relative_improvements': improvements})
        if not reasons:
            accepted.append(candidate)
    # Automatic selection must not worsen either preference against the incumbent.
    # Among improvements, apply the agreed priority order rather than requiring
    # one candidate to dominate every other candidate. Regressing alternatives
    # remain reviewable; an exact tie with the incumbent never replaces it.
    improving = [c for c in accepted if c.accuracy >= baseline.accuracy and
                 getattr(c, cost_metric) <= getattr(baseline, cost_metric) and
                 c.latency_p95_s <= baseline.latency_p95_s and
                 (c.accuracy > baseline.accuracy or
                  getattr(c, cost_metric) < getattr(baseline, cost_metric) or
                  c.latency_p95_s < baseline.latency_p95_s)]
    # Prompt text only breaks an exact three-metric tie, so candidate submission
    # order cannot change the selected prompt. It has no bearing on performance.
    ranked = sorted(improving, key=lambda c: (-c.accuracy, getattr(c, cost_metric),
                                            c.latency_p95_s, c.prompt))
    ranks = {id(candidate): rank for rank, candidate in enumerate(ranked, 1)}
    winner = ranked[0] if ranked else baseline
    cost_label = 'cost per success' if cost_metric == 'cost_usd_per_success' else 'cost per attempt'
    for candidate, decision in zip(candidates, decisions):
        decision['selection_rule'] = SELECTION_RULE
        decision['selection_cost_metric'] = cost_metric
        decision['rank'] = ranks.get(id(candidate))
        decision['selected'] = candidate is winner and winner is not baseline
        decision['needs_review'] = False
        if decision['selected']:
            decision['notes'] = [f'Selected by highest accuracy, then lowest {cost_label}, then lowest p95 latency.']
        elif id(candidate) in ranks:
            decision['notes'] = [f'Eligible improvement ranked below the selected prompt by accuracy, {cost_label}, then p95 latency.']
            if candidate.latency_p95_s < winner.latency_p95_s:
                decision['notes'].append('Faster alternative: lower p95 latency than the selected prompt.')
            continue
        if not decision['selected'] and decision['accepted']:
            dominated = any(other.accuracy >= candidate.accuracy and
                            getattr(other, cost_metric) <= getattr(candidate, cost_metric) and
                            other.latency_p95_s <= candidate.latency_p95_s and
                            (other.accuracy > candidate.accuracy or
                             getattr(other, cost_metric) < getattr(candidate, cost_metric) or
                             other.latency_p95_s < candidate.latency_p95_s)
                            for other in [baseline] + accepted)
            unchanged = (candidate.accuracy == baseline.accuracy and
                         getattr(candidate, cost_metric) == getattr(baseline, cost_metric) and
                         candidate.latency_p95_s == baseline.latency_p95_s)
            decision['needs_review'] = not dominated and not unchanged
            decision['notes'] = (['acceptable tradeoff; customer review needed']
                                 if decision['needs_review'] else ['no observed advantage over the incumbent or an accepted candidate'])
    return winner, decisions
