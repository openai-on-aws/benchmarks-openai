# AWS controls and offline execution

The task is `benchmarks/aws-cdk-smoke/cdk-sqs-lambda-dynamodb`, relative to the
plugin root. Its `instruction.md` defines the contract. The packaged
`solution/stack.ts` and `solution/handler.js` supply the positive control.
The alternate uses a different handler loop, batch size 1, and 240-second
visibility. It also uses mixed-case valid DynamoDB/SQS actions and an explicit
Lambda role with inline CreateLogGroup, CreateLogStream, and PutLogEvents
permissions equivalent to the basic logging managed policy. These positive
controls detect blanket rejection of casing variants or non-DynamoDB/SQS
service prefixes. The unchanged broken project is a negative control.

Every mutation is a literal, validated patch against that correct solution.
[cdk-mutations.json](../assets/cdk-mutations.json) contains the executable
matrix. [alternate-stack.ts](../assets/alternate-stack.ts) and
[alternate-handler.js](../assets/alternate-handler.js) are the second correct
implementation. [cdk_controls.py](../scripts/cdk_controls.py)
materializes complete candidate projects and invokes the real verifier.
Only `lib/stack.ts` and `lambda/handler.js` differ from the packaged candidate
project; expected outcomes and verification code stay outside it.

| Invariant | Negative controls | Positive coverage |
| --- | --- | --- |
| Existing table identity | `table-identity` | Original `Records` construct |
| Table name, key, billing, encryption | `table-name`, `table-key`, `table-billing`, `table-encryption` | Full original resource contract |
| Data retention | `table-deletion-policy`, `table-replacement-policy` | Both policies remain Retain |
| Table write scope | `iam-wildcard-action`, `iam-wildcard-resource`, `iam-missing-write` | Canonical and mixed-case PutItem only on the table |
| Queue permissions | `iam-extra-sqs-action`, `wrong-event-queue` | Canonical and mixed-case SQS consumer grants on the work queue |
| IAM bypasses and unrelated grants | `iam-admin-managed-policy`, `iam-not-action`, `iam-mixed-case-wildcard`, `iam-unrelated-service` | Task permissions plus managed or equivalent inline basic logging |
| SQS → Lambda → table wiring | `missing-mapping`, `wrong-event-queue`, `wrong-table-environment` | Work queue mapping and table environment reference |
| Batch/retry configuration | `partial-reporting-disabled`, `batch-size-eleven`, `short-visibility`, `changed-lambda-timeout`, `changed-redrive-count` | Batch sizes 1 and 10, visibility 180 and 240, timeout 30, redrive count 3 |
| Process the entire batch | `handler-first-record`, `handler-truncate-long-batch` | Multiple writes, including an independent 10-record fixture check |
| Preserve message data | `handler-drop-extra-fields` | Independent nested/additional payload fields check |
| Partial failure reporting | `handler-swallow-failure`, `handler-stop-on-failure`, `handler-wrong-failure-id` | Invalid JSON, invalid fields, a failed write, and subsequent success |
| Runtime write behavior | `handler-hardcoded-table`, `handler-retry-write`, `handler-accept-string-value`, `malformed-handler` | Environment table, PutCommand, no handler retries, valid CommonJS |

The [IAM Action element documentation](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements_action.html)
defines service prefixes and action names as case insensitive. The
[Lambda logging documentation](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs.html)
lists CreateLogGroup, CreateLogStream, and PutLogEvents as the basic logging
permissions supplied by AWSLambdaBasicExecutionRole. The alternate valid
control exercises those same permissions inline, including casing variants.

The long-batch, additional-field, mixed-case IAM, and unrelated `iam:PassRole`
controls probe common omissions in example-based graders. Their
expected outcome is rejection by the task contract. Only execution establishes
whether they survive this verifier version; do not predeclare them killed.
Keep survivors visible for a separately scoped grader improvement.

## Runtime contract

Use existing dependencies; the audit performs no preparation or installation.
With Node.js 22+ on PATH, `--tools-dir DIRECTORY` checks:

1. `DIRECTORY/node_modules`
2. `DIRECTORY/aws-cdk-smoke/node_modules`

The directory must contain the exact direct dependencies and devDependencies
from the packaged `environment/project/package.json`. A preflight imports the
CDK and SDK libraries and records their versions. If a neighboring
`package-lock.json` exists, the audit records its hash and whether it matches
the packaged lockfile. It does not copy or remove the prepared runtime.

Each candidate receives a dependency symlink and separate `dist/` and `cdk.out/`
outputs. A trusted driver invokes the installed TypeScript compiler and the
packaged compiled `bin/app.js`, which calls `app.synth()`. It then runs an
unchanged copy of `tests/verify.cjs` using `BENCH_PROJECT`. This exercises the
actual grading logic. It bypasses the CLI shell launcher and Harbor reward-file
transport; it does not execute `package.json` scripts or `cdk.json` commands.

For example, from the plugin's skill directory:

```bash
python3 -B ../../scripts/bench.py audit --suite aws-cdk-smoke \
  --tools-dir /absolute/path/to/prepared-cdk-runtime \
  --out /absolute/path/to/new-audit --execute
```

The skill helper also works without CLI integration:

```bash
python3 -B scripts/cdk_controls.py --tools-dir /absolute/path/to/prepared-cdk-runtime \
  --out /absolute/path/to/another-new-audit --execute
```

Alternatively, put the immutable ID of an **already built** smoke image in
`DIRECTORY/image-id.txt` or `DIRECTORY/aws-cdk-smoke/image-id.txt`. The file
accepts only `sha256:` followed by 64 lowercase hex characters, never tags,
commands, or extra options. The image must have the packaged Node dependencies
at `/app/node_modules`. The controller requires a local Unix Docker socket and
runs with a fixed Node entrypoint, `--pull=never`, `--network=none`, a read-only
root/controller, and separate candidate mount. On Unix it uses the caller's
UID/GID so generated artifacts remain owned by that user. No image discovery,
build, or pull occurs. Local Node is preferred when both runtimes are specified.

All subprocesses have time and output limits. A timed-out Docker process is
removed using the container ID written by that invocation's Docker client;
cleanup evidence is retained. Node network and child-process
entrypoints are blocked for these authored controls, and credentials and
startup hooks are not inherited. This is not a general security sandbox for
hostile candidate code.

## Evidence interpretation

Build/synth errors, driver protocol errors, unexpected verifier exceptions,
timeouts, and missing dependencies never count as killed mutants. Missing or
incompatible optional runtime preparation yields `unavailable`; a failure after
successful preflight is an infrastructure error and makes the audit `failed`.
The driver recognizes assertion failures and syntax/load errors attributable
to the submitted handler as grader rejection; other errors fail closed.

[check_handlers.cjs](../scripts/check_handlers.cjs) checks the authored handler
fixtures with a tiny SDK double and additional inputs. It is explicitly labeled
`fixture-self-check`, never a replacement CDK grader. Its outcomes do not enter
the actual grader counts. Even when this check passes, absent CDK dependencies
still leave the audit `unavailable`.
