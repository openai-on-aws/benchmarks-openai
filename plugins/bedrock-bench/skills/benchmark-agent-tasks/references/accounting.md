# Cost accounting reference

`cost_per_success = total spend across all attempts / successful attempts`.
Failures contribute to the numerator. Zero successes produces no finite cost
per success. Missing usage/cost produces `null`, with a known-cost subtotal
and cost coverage reported separately.

Cost bases:

- `provider_reported`: OpenRouter's reported account charge, excluding any
  separately reported upstream cost from the total to avoid double counting.
- `rate_card_estimate`: an explicit rate card applied to recorded usage.
- `runner_estimate`: OpenCode's emitted catalog-based estimate.
- `synthetic`: invented demonstration data.
- `reference_no_model`: a reference/oracle check, excluded from model comparisons.
- `unknown`: insufficient pricing or usage evidence.

An OpenCode estimate of zero can mean missing catalog prices. It remains
unknown unless an explicit rate card supports the cost calculation.

Codex token usage does not reveal a subscription's marginal dollar cost.
For API-equivalent estimates, add a rate card matching the exact provider,
model, region (or `null`), and service tier. No default model rate cards are bundled in the plugin.

Each entry in `rate_cards` requires:

| Field | Meaning |
|---|---|
| `provider`, `model`, `region`, `service_tier` | Exact target identity |
| `as_of` | Source date, `YYYY-MM-DD` |
| `source` | HTTPS pricing reference |
| `input_usd_per_million` | Ordinary input rate |
| `output_usd_per_million` | Output rate, including reasoning where reported in output |
| `cached_input_usd_per_million` | Required to price observed cache reads |
| `cache_write_input_usd_per_million` | Required to price observed cache writes |
| `max_input_tokens` | Optional applicability boundary for a flat context-price band |

Input totals include cache reads/writes; output totals include reasoning.
Pricing subtracts cached/write tokens from ordinary input before charging
their separate rates. Reasoning tokens are not added again to output.
Missing cache details or an exceeded context-price boundary leaves the estimate
unknown. OpenCode totals are normalized from its separately reported subsets.
Configure a price band that actually applies to the task context; conditional
pricing not expressed in a rate card is outside the first release.

Inference costs exclude runner infrastructure, subscriptions, external tools,
and judges. CLI usage events may omit auxiliary model calls, such as client
housekeeping. These reports account for emitted usage, not a reconciled
account bill. No model-based judge is used by the starter suite.

For repository suites, a positive upstream cost estimate is retained when
available. A matching rate card is a fallback when the runner has no usable
estimate; it does not override a positive runner estimate. The short-context
price guard is applied to aggregate upstream input usage, so long trials can
remain unpriced even if individual requests fit that price band.

See [Repository suites](suites.md#results) for upstream result import and
[CLI and configuration](cli.md) for experiment setup.
