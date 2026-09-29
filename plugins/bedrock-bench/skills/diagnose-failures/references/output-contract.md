# Diagnosis output contract

`write_diagnosis(sources, directory, *, limit=100) -> pathlib.Path` writes
`DIAGNOSIS.json` and `DIAGNOSIS.md` and returns the absolute Markdown path.
`diagnosis_data(sources, *, limit=100)` returns the same JSON-compatible data
without writing. Both functions are in the installed
`scripts/bedrock_bench/diagnosis.py` module.

Sources are one path or an iterable of explicit schema-version-1 run files or
individual run directories containing `run.json`. There is no recursive
discovery and no automatic choice between original and accounting-reviewed
copies. Repeated paths and identical copies of the same run are counted once,
with provenance notices. Conflicting copies of one run ID or attempt ID are
rejected so the caller can select the intended source. Identical repeated
attempt IDs are counted once. Distinct attempt IDs remain distinct observations;
repeated schedule slots are flagged.

An explicitly selected source with `accounting_review` metadata, or named
`run-accounting-reviewed.json`, must pass the library's provenance rules using
bounded reads. Its `original_run` must name a file beside the selected source,
`original_sha256` must match those original bytes, and its cited audit `source`
must be a contained regular file. Run fields other than `name`, `attempts`, and
`accounting_review` must remain unchanged, with equal attempt counts and order.
Within attempts, only the library's `ACCOUNTING_FIELDS` may differ: `usage`,
`usage_granularity`, `reported_usage_events`, `cost_usd`,
`known_cost_subtotal_usd`, `cost_basis`, `accounting_complete`, and
`accounting_review`. Outcomes, targets, timing, and evidence paths cannot change.
Non-accounting comparisons preserve JSON types, so replacing a boolean with a
numeric zero or one is a change.

Invalid proofs are rejected, including a missing original or audit file,
symlinks, checksum mismatches, and non-accounting mutations. The selected
reviewed source remains the source of diagnosis; neither fallback to the
original nor automatic selection of a sibling review occurs. Review provenance
records original and audit paths, byte counts, and SHA-256 hashes. The audit's
contents are not projected or used to audit pricing; `pricing_audited` is false.

The destination may exist, but neither output file may already exist, including
symlinks. All validation and aggregation precede writing. Sources, traces, and
previous reports are never overwritten.

## Data fields

The JSON root has `schema_version: 1` and
`artifact: "bedrock-bench-diagnosis"`.

| Field | Meaning |
| --- | --- |
| `counts` | Full population counts across accepted sources. Root cost amounts and coverage are null when evidence kinds differ. |
| `cost_aggregation` | Whether root cost aggregation is permitted, selected evidence kinds, the reason for withholding, and the withheld field names. |
| `runs` | Exact run IDs, source paths/digests, optional verified `accounting_review` provenance, status, evidence labels, schedule coverage, counts, and notice codes. |
| `partitions` | Evidence kind + protocol + suite, with run IDs and counts. A missing protocol gets its own run-specific partition. |
| `models` | Execution identity lookup by `model_key`, including runner/provider/model/version/settings. Friendly target aliases do not change identity. Unrecorded versions remain unknown. |
| `records` | One compact row per unique recorded attempt or identifiable missing schedule slot, **unaffected by `limit`**. Contains provenance, model/task/repetition, outcome, one category, confidence, costs, and notices. |
| `details` | At most `limit` evidence projections, selected in source order within failure, interruption, unknown, missing, and success priority. Join to `records` by `key`. |
| `by_category`, `by_model`, `by_task` | Full count and accounting aggregates within each `partition_id`. |
| `cells` | Full `partition_id × model_key × task_id × category` aggregates for heatmaps and Pareto charts. |
| `warnings` | Fixed notice codes, affected item counts, and descriptions; no raw exception text. |
| `duplicate_sources` | Skipped source paths, source digests, and run IDs. |
| `coverage` | Detail omissions, runs with unknown missing-attempt counts, byte budgets, and bytes read. `source_bytes` includes selected runs and review-proof original/audit files. |
| `taxonomy` | Definitions of the observation categories. |

Every compact record has an exact `attempt_id` when the source records it;
otherwise it is null. `attempt_index` identifies a zero-based position in the
source's attempts array. Missing slots retain target/task/repetition and any
explicit scheduled attempt ID; generated attempt IDs are never invented.
Sources are hashed as bytes. Model/partition/record keys are stable opaque
digests; they are not evidence checksums.

Evidence projections contain the contained source path, byte count, observed
SHA-256, recorded SHA-256 when present, status, and public field/line citations.
Line numbers are one-based. The `field` identifies a JSON field within that
record; dotted subfields in error objects denote allowlisted scalar fields.
Files that could not safely be read have no invented checksum. A checksum
mismatch prevents use of that file's signatures.

Identifiers and paths are preserved except for recognized credential values,
which are redacted. JSON carries full precision. Markdown renders labels as
inert text and rounds display dollars; use JSON for calculations.

## Outcomes, schedule coverage, and accounting

`attempts = successes + failures + interrupted_attempts + missing_attempts +
unknown_outcomes`. `recorded_attempts` excludes missing slots.
Explicit attempt cancellation/interruption is separate from failures. A run's
interrupted status does not relabel completed attempts. Contradictory success
and status fields produce unknown outcomes.

Expected slots come from the explicit `schedule`, or the saved experiment's
targets × tasks × repetitions. Recorded target/task/repetition triples are
matched to those slots. Without a schedule/grid, or when some recorded rows
lack matching identity fields, `missing_attempts_known` is false and no missing
identities are manufactured. Check that flag and
`coverage.runs_with_unknown_missing_attempts` before claiming complete planned
coverage. Attempts outside the saved schedule are retained with a notice.

All aggregates include:

- `known_cost_attempts`, `unknown_cost_attempts`, and `cost_coverage` over all
  recorded plus identified missing attempts; `recorded_cost_coverage` uses only
  recorded attempts.
- `total_cost_usd`, null if any included attempt lacks a complete cost, and
  `known_cost_subtotal_usd`, a lower bound from reported known components.
- `failed_attempt_cost_usd`, null if any failure lacks complete cost;
  `failed_attempt_known_cost_subtotal_usd`, `failed_attempt_unknown_costs`, and
  `failed_attempt_cost_coverage`. This failure subset excludes interruptions,
  unobserved slots, and unknown outcomes; those remain in overall accounting.
- `cost_basis`, preserving estimates, synthetic/reference costs, and unknowns.

When selected evidence kinds differ, the root `counts` withholds all four USD
amounts above plus `cost_coverage`, `recorded_cost_coverage`, and
`failed_attempt_cost_coverage`. `cost_aggregation.status` is `"withheld"`,
with a reason and the exact `withheld_fields`. Population and cost-availability
counts remain intact, including known/unknown cost attempt counts; withholding
does not relabel known costs as missing. Cost basis labels remain descriptive
metadata. Per-run, per-partition, category, model, task, and cell accounting
remain available within their evidence partitions. A mixed synthetic/live
inventory must never display a combined subtotal as actual spend.

For one evidence kind, `cost_aggregation.status` is `"same_evidence_kind"` and
normal complete/unknown cost rules apply, even across different protocols.
Such totals remain a descriptive inventory, not a performance comparison.

Zero observations have zero known spend and unavailable cost coverage.
Missing slots have unknown cost, not zero full cost. An explicitly incomplete
accounting flag prevents a numeric cost from being treated as complete.
Invalid negative, boolean, non-finite, or inconsistent subtotals are rejected;
non-finite aggregate sums are rejected as well. Costs are recorded inference
costs with the plugin's infrastructure/tool/subscription/judge exclusions.

## Limits and incomplete evidence

`limit` is an integer from 0 through 1,000 (default 100); zero produces aggregate
and compact-record output without details. Up to 1,000 supplied sources and
100,000 total recorded plus inferred missing attempts are accepted. Input JSON
is bounded to 32 MiB per file and 128 MiB overall, with strict finite numbers
and unique object keys. Invalid schemas or capacity overflow fail explicitly
before output, rather than silently reducing the population.
Review-proof original runs share those per-file and total source limits;
cited audit files are additionally bounded to 2 MiB each and count toward the
same 128 MiB source budget. Unlike optional traces, an unavailable or oversized
required review proof rejects the selected reviewed source.

Only recorded `trace` and upstream `result` paths are examined, inside their
source run directory. Traversal, absolute evidence references, symlinks, and
non-regular files are rejected. There is no session-directory scan or reading
of arbitrary paths named inside a trace.

Reads are bounded to 2 MiB per evidence file and 64 MiB overall. JSONL parsing is
bounded to 20,000 events, 64 KiB per event, and 24 projected signals per attempt.
Runner errors are bounded to 16 entries and 4,096 characters per allowlisted
scalar field. The module never projects free-form error messages; it emits
fixed signature labels with field citations. Unsupported, malformed, missing,
unsafe, checksum-mismatched, or truncated evidence produces notices and retains
the attempt. Budget-limited categories reflect available status/signatures;
they must not be presented as an exhaustive causal analysis.

## Plotting without inflating counts

Pass this JSON to `$bedrock-bench:visualize-results`. Use `cells` for a heatmap,
`by_category` for a category Pareto chart, or group the full `records` array.
Filter or facet by `partition_id`; keep protocol and evidence labels visible.
For failure-only views select `outcome == "failure"` or use the aggregate's
`failures` field. Show missing/unknown/interruptions separately.

Each attempt has one primary category. Do not count its multiple `signals` as
multiple failed attempts, use the bounded `details` as the denominator, turn
unknown cost into zero, or interpret cross-protocol frequencies as performance
rankings. A selected cell should link its record keys back to exact run/attempt
provenance and show evidence notices and cost coverage.
