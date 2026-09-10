# Benchmark sources and attribution

The links below identify the benchmark sources used by this comparison. Sample sizes are the evaluated counts per model/reasoning configuration, not necessarily the size of the upstream dataset.

For the exact pinned versions, splits, selection procedures, seeds, and available source identifiers, see [dataset selection](evidence/dataset-selection.json) and the [methodology](METHODOLOGY.md#public-dataset-selection). A seed used to select cases is distinct from seeds used for stage membership and execution order.

| Workload | N | Upstream reference |
|---|---:|---|
| Synthetic classification/routing | 192 | Project-authored synthetic evaluation |
| Synthetic invoice extraction | 192 | Project-authored synthetic evaluation |
| Banking77 | 3080 | [Banking77](https://github.com/PolyAI-LDN/task-specific-datasets) |
| CORD v2 original images | 100 | [CORD v2 original images](https://github.com/clovaai/cord) |
| CORD v2 provided OCR | 100 | [CORD v2 provided OCR](https://github.com/clovaai/cord) |
| ExtractBench | 370 | [ExtractBench](https://huggingface.co/datasets/llamaindex/ExtractBench) |
| GSM8K | 100 | [GSM8K](https://huggingface.co/datasets/openai/gsm8k) |
| MATH-500 | 100 | [MATH-500](https://huggingface.co/datasets/HuggingFaceH4/MATH-500) |
| AIME 1983–2024 mirror | 60 | [AIME 1983–2024 mirror](https://huggingface.co/datasets/qq8933/AIME_1983_2024) |
| GPQA Diamond | 198 | [GPQA Diamond](https://huggingface.co/datasets/Idavidrein/gpqa) |
| MMLU-Pro | 140 | [MMLU-Pro](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro) |
| HumanEval | 164 | [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval) |

## Scope and rights

This package includes aggregate measurements, scoring definitions, a report renderer, and an illustrative address-formatting example. It does not distribute benchmark rows, source PDFs/images, prompts, expected answers, or per-case model responses.

The existing repository [code license](../../LICENSE) and [documentation license](../../LICENSE-DOCS.md) are unchanged. They do not establish rights in third-party benchmark content. The applicability of licenses, attribution/NOTICE requirements, and permission to release this mixed code/report/data contribution remain subject to the release review.

The AIME link is a dataset mirror; its metadata alone does not establish rights in the underlying contest problems. GPQA source access conditions also remain applicable. No source-data redistribution permission is asserted by this file.

The public source links are references, not a substitute for exact evaluation inputs. The selection metadata adds public-source pins and case-selection details without redistributing examples or responses. Full source/input mapping and the original evidence remain in the internal review record; the public package reproduces aggregate reporting only.
