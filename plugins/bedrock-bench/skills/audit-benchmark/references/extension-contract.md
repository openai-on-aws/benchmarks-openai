# API, evidence, and extension contract

The parent CLI calls:

```python
write_audit(suite, directory, *, seed=42, execute=False, tools_dir=".bench-tools") -> pathlib.Path
```

The returned path is `directory/AUDIT.md`; sibling `AUDIT.json` is the
authoritative report. Supported suites are `starter` and `aws-cdk-smoke`.
The seed must be an integer excluding booleans, and execute must be a boolean.
Output must be a new or empty directory outside the installed plugin and
runtime tree. Traversal, control characters, symlink output roots, non-directory
tool roots, and artifact collisions are errors. Relative paths resolve from
the caller's working directory. No evidence or candidate is overwritten.

CLI exit codes are 0 for `planned`/`passed`, 1 for `failed`, and 2 for
`unavailable` or invalid arguments. A nonzero audit result is evidence to
inspect, not permission to change the production grader.

## AUDIT.json schema version 1

| Field | Meaning |
| --- | --- |
| `schema_version`, `audit_id`, `suite`, `seed` | Report identity and fixed input selection |
| `status` | `planned`, `passed`, `failed`, or `unavailable` |
| `kind`, `synthetic`, `validation_only`, `model` | `grader-audit`, true, true, null; no model measurement |
| `execute` | Whether grading was requested |
| `scope` | Exact execution boundary and limits of the conclusions |
| `source_hashes` | Plugin-relative source path → SHA-256 of source bytes |
| `grader_hashes` | Plugin-relative grader path → SHA-256; includes the actual verifier |
| `controller_hashes` | SHA-256 of audit code and shipped control resources |
| `cases` | Exact control list, including unexecuted and failed controls |
| `plan` | Fixed execution argv targeting a distinct output directory; informational, never executed automatically |
| `summary` | Matched count and IDs of surviving mutants, killed mutants, rejected valid answers, infrastructure errors, and unavailable controls |
| `preparation_needed` | Concrete reasons and existing-runtime requirements |
| `runtime`, `runtime_contract` | CDK runtime selection, verified versions, and offline invocation contract |
| `plan_validation` | CDK literal-patch/materialization checks; never a grader pass |
| `fixture_validation` | Optional independent handler-fixture self-check; excluded from grader counts |

Each case contains:

| Field | Meaning |
| --- | --- |
| `id`, `task`, `name`, `kind` | Stable suite-local control identity; kind is reference, alternate-valid, semantic-mutant, malformed, or baseline |
| `label`, `model` | reference or synthetic, and null; never model |
| `invariant` | The requirement this control exercises |
| `expected` | `accept` or `reject`, declared before grading |
| `observed` | `not_run`, `accept`, `reject`, `infrastructure_error`, or `unavailable` |
| `status` | planned, passed, failed, or unavailable |
| `candidate` | Audit-relative directory containing only task inputs/submission/build outputs |
| `files` | Exact initial candidate file hashes/sizes, or the explicit symlink recipe |
| `recipe` | Input seed, base solution, and exact patches/artifact locations |
| `reason` | Actual grader or infrastructure explanation when executed |
| `evidence` | Audit-relative paths and SHA-256 of controller records, verdicts, templates, and phase logs |

Only `observed == expected` passes a case. A rejected valid answer, surviving
mutant, or execution infrastructure error makes the audit failed. A missing
runtime makes controls unavailable. Infrastructure errors cannot be coerced
into `reject`, even if an external process exited nonzero. All positive and
negative controls must have valid outcomes before the whole audit can pass.

## Adding a control or suite

1. Identify the task contract and real grader entrypoint. Hash the actual source,
   fixture inputs, grader, and audit resources. Derive a correct solution from
   visible inputs or a separately validated reference; do not copy a grader's
   expected answer as the independent oracle.
2. Add a correct reference and, where the contract allows it, another correct
   implementation or serialization. Include missing/malformed output, then
   deliberate semantic mutations with a single explainable defect. A merely
   different answer is not automatically wrong.
3. Materialize fixed candidate artifacts and declare expected outcomes before
   execution. Keep controller recipes, hidden answers, and evidence outside the
   candidate. Reject no-op patches, source drift, unknown paths, and duplicate
   case IDs. New suites need a closed adapter; user configs and trace content
   must not define executable commands.
4. Exercise the unchanged production grader. Keep build, setup, process,
   parser, and transport failures distinct from genuine grader rejection.
   Check that an always-pass grader fails the negative controls and an
   always-fail grader fails the positive controls.
5. Validate the control's defect independently where feasible, and label that
   check separately. Store exact observed outcomes and evidence without
   adjusting expectations to match the grader. Do not edit a production grader
   as part of satisfying its audit.
6. Test dry-run behavior, known positive/negative outcomes, surviving mutants,
   infrastructure failures, invalid options/path containment, no overwrite,
   and execution from a copied installable plugin with no repository neighbor.

The starter reference solver reads only the visible task input files. Its
semantic mutants cover reconciliation, dependency ordering, and retry
accounting. Equivalent formatting is valid for every task, and the dependency
task has a distinct alternate topological order. The task's extra-key rule is
applied only where the prompt says “exactly”; do not flag the deployment
grader's extra-key tolerance as a false positive.
