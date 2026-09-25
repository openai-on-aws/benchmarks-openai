# Bedrock Bench development

This directory is an independently installable plugin. Its implementation lives
in `scripts/bedrock_bench`; `scripts/bench.py` is the packaged entrypoint. The
repository-root `bench.py` forwards to it. Runtime code and skill references
must work from the installed plugin cache without a neighboring source checkout.

Keep documentation focused on its reader:

- `README.md`: human onboarding, installation, first report, and chat examples.
- `skills/benchmark-agent-tasks/SKILL.md`: agent workflow and execution guidance.
- `skills/benchmark-agent-tasks/references/`: conditional CLI, suite, and
  accounting details.
- This file: contributor guidance for changes to the plugin.

For runtime or adapter changes, run the relevant checks from the repository root:

```bash
python3.12 -B -m unittest discover -s tests -p 'test_bedrock_bench*.py' -v
```

The two installed-harness schema checks need prepared Harbor/AWS-Bench
environments in `.bench-tools`. For documentation changes, check referenced
paths and anchors; validate skill frontmatter when changing the skill entrypoint.
Reference smoke checks and live model measurements are different validation
stages; describe which stage was actually exercised.
