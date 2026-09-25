# Upstream benchmark sources

The plugin calls upstream harnesses rather than copying their implementations.
The packaged registry snapshots contain task names and pinned source
descriptors from these projects, retrieved on 2026-09-25:

- [Harbor](https://github.com/harbor-framework/harbor), Apache-2.0:
  [registry](https://github.com/harbor-framework/harbor/blob/main/registry.json).
  Runtime package pinned to `harbor==0.23.0`.
- [Terminal-Bench 2.0](https://github.com/harbor-framework/terminal-bench-2),
  Apache-2.0. Task commit:
  `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`.
- [Harbor's SWE-bench Verified adapter](https://github.com/harbor-framework/harbor/tree/main/adapters/swebench).
  Converted task commit in `laude-institute/harbor-datasets`:
  `86723674f04e4209ac479d0fb75d9d9f44b4377e`.
  [SWE-bench](https://github.com/SWE-bench/SWE-bench) is MIT-licensed; the
  underlying benchmark repositories and container images retain their licenses.
- [AWS-Bench](https://github.com/aws-bench/aws-bench), Apache-2.0:
  runtime commit `ea65432b5ce1d838b932728fcec4e02f493761a5` (package 0.7.0,
  with Harbor 0.9.0).
- [AWS-Bench datasets](https://github.com/aws-bench/aws-bench-datasets),
  Apache-2.0: quickstart 0.7.2, task/scenario commit
  `a45996f548e588d1a6c4c2faeeeaac1b177e83fe`.

`registry.json` supports offline task discovery. `aws-registry.json` retains
the quickstart's scenario, instruction, and metric descriptors needed by the
AWS-Bench environment manager. It is not a Codex plugin marketplace.

Updating snapshots should be an intentional code change: review changed
tasks and licenses, regenerate the pinned metadata, then validate both
upstream schemas and reference smoke runs. Runtime versions are recorded in
each result. Some upstream Dockerfiles reference image tags and download
dependencies at setup time; pinned task commits alone do not guarantee
bit-for-bit identical upstream environments.

Our `aws-cdk-smoke` task is original code under the plugin's MIT-0 license.
Its agent and verifier use pinned npm dependencies and separate containers.
