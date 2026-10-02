# GPT-6 Astra Standard vs Ultrafast latency test plan

Prepared October 2, 2026. Status: proposed experiment; no live calls or results.

This plan supports the OpenAI on Bedrock blog draft. It uses this repository's Responses API streaming harness. The runner and report changes listed below are required before collecting a valid tier comparison.

## Question and comparison

How do Standard and Ultrafast affect first-text latency, visible output throughput, and response completion time for GPT-6 Astra on selected text-generation prompts?

| Setting | Standard | Ultrafast |
| --- | --- | --- |
| Backend | `bedrock-runtime` | `bedrock-runtime` |
| Base URL | `https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1` | Same |
| Model / profile | `us.openai.gpt-6-astra` | Same |
| Requested `service_tier` | `default` | `ultrafast` |
| Reasoning effort | `low` | `low` |
| Response mode | Streaming Responses API, text only | Same |
| Maximum output tokens | 2,048 | 2,048 |
| Concurrency | 1 | 1 |

The service tier is the variable under comparison. Keep the client machine, connection policy, AWS account/project, SDK version, prompt bytes, and remaining request parameters fixed. Use the same source Region and routing profile throughout. A US inference profile can route across US Regions, so `us-east-1` identifies the source endpoint, not a guaranteed inference destination.

Run from one stable host. Record its location, instance type if applicable, OS, Python and SDK versions, and the test window. Laptop measurements include the laptop's network path. Keep a separate configuration if you later test from an AWS-hosted client.

## Prompts and run counts

Start with the repository's existing `performance/data/prompt_1k.txt` and `performance/data/prompt_10k.txt`, including the instruction appended by the harness. Record hashes of the complete requests and actual API-reported input counts. The size labels are approximate.

These are two text-generation fixtures. Repeating them measures variability for those inputs; it does not establish performance across all coding or business tasks.

| Stage | Measured requests per tier per fixture | Warmups per tier per fixture | Measured total | Warmup total | Total requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pilot | 5 | 2 | 20 | 8 | 28 |
| Main comparison, after reviewing the pilot | 100 | 5 | 400 | 20 | 420 |

The pilot checks access, tier selection, streaming, usage fields, and whether the output budget produces enough visible text. Its sample size is not suitable for a strong tail-latency claim. The main count is a practical starting budget, not a guarantee of statistical power. Pilot and main together would make 448 requests if both are run exactly as planned.

For each fixture, pair the two tiers on the same input and randomize which tier goes first with a saved seed. Interleave the pairs over the run rather than collecting all Standard requests and then all Ultrafast requests. Record pair IDs and order.

Use warmups to establish the chosen connection and repeated-context conditions. Record cache configuration and API-reported cache usage. If cache behavior differs between the tiers, show separate cache groups and disclose the difference. Do not attribute a combined cache-and-tier effect entirely to Ultrafast. A separately controlled cold-cache study can follow if the selected route supports the necessary controls.

Stop at the planned count. Retain failed and partial attempts instead of replacing them until a target number of successes is reached.

## Metrics and definitions

Use a monotonic clock. Record request start, first server event, first nonempty visible-text delta, last visible-text delta, terminal response event, and stream close.

For these text-only requests, calculate visible output tokens from API usage as total output tokens minus reasoning tokens. If the needed usage fields are missing or inconsistent, mark visible throughput as unavailable. Do not silently assume missing reasoning usage is zero.

| Metric | Definition and interpretation |
| --- | --- |
| Time to first server event (TTFE) | Request start to the first event of any type. Useful for separating initial response activity from visible text. |
| Time to first visible text (TTFT) | Request start to the first nonempty `response.output_text.delta`. This is the reader's initial wait. |
| Visible streaming rate, estimated tokens/s | Visible output tokens divided by time from first visible text to stream close, matching the current harness's OTPS interval. This client-observed estimate includes tokens in the first chunk and final stream overhead. Require at least two text deltas; report single-flush responses as unavailable. |
| Visible output rate over the full request | Visible output tokens divided by request-start-to-stream-close time. This includes the initial wait. |
| Inter-chunk gaps | Time between successive nonempty text deltas. SSE chunks can contain multiple tokens, so these are not exact inter-token latencies. |
| Full response time | Request start to stream close. Record terminal status alongside it. |
| End-to-end time with retries | If a later test allows retries, measure the whole logical request, including every attempt and backoff. Keep this distinct from successful-attempt latency. |
| Completion and failure counts | Separate completed responses, output-limit truncations, refusals where observable, failed responses, transport errors, timeouts, and missing visible text. |
| Usage and estimated cost | Record actual input, cached input, cache writes where exposed, output, and reasoning tokens for every attempt. Price with the matching tier and routing option. |

The gap between TTFE and TTFT is an observable waiting interval, not a direct measurement of internal reasoning time. Likewise, visible streaming rate does not measure hidden reasoning-token generation speed.

A maximum output budget does not guarantee equal output lengths. Show actual visible and reasoning-token distributions. A response truncated at the output limit can inform a generation-rate test, but it must not be counted as a successfully completed user task.

Report p50 and p95 for TTFT and full response time, and p50 plus the distribution for throughput. Show metric-specific sample counts, missing values, and failure rates. Treat p95 as an estimate with uncertainty; do not headline p99 from a small run.

For each fixture, compare matched pairs and report uncertainty over the sampled requests. Keep the scope limited to those fixtures and the recorded time window. Calculate latency improvement as `1 - ultrafast / standard`, and throughput speedup as `ultrafast / standard`, identifying whether each comparison uses medians or paired ratios.

## Changes required in the existing harness

The inspected local working copy already has TTFE, visible TTFT, reasoning-token counts, and streaming timing. It is not ready to distinguish the two service tiers:

1. Add explicit service-tier selection to `performance/benchmark.py` and forward it as the Responses API `service_tier` field. Record the requested tier in every attempt and the returned tier when exposed. Flag a mismatch. If the service does not return a tier, state that limitation and preserve evidence of the request configuration.
2. Add the tier to result filenames, metadata, and comparison grouping. Save pair IDs, prompt hashes, timestamps, completion status/reason, usage, and SDK versions. Record the harness commit and a content hash for any uncommitted harness changes. A filename tag alone does not establish which tier was sent.
3. Update `performance/compare.py` to select two tiers of the same backend and model. Its current grouping omits tier, Region, endpoint, cache policy, and prompt hash, so new runs could otherwise replace or be mixed with each other.
4. Add a report path for this comparison. The current `performance/report.py` is oriented around older model families and Bedrock versus OpenAI direct; it skips Runtime latency files and explicit reasoning-effort runs.
5. Disable SDK automatic retries and the outer retry loop for the primary per-attempt latency test. Save failures and partial measurements. If retry behavior is evaluated later, capture all attempts and total elapsed time; the current outer loop records the final attempt's latency and can hide earlier waits.
6. Treat terminal response status as part of validity. The current summary selects requests by the absence of a Python error, which can include incomplete or failed terminal responses. Preserve these categories and their counts.
7. Add an interleaved run schedule and save partial results after each request. Check offline with simulated streams covering multi-token chunks, a single text flush, reasoning-only output, missing usage, truncation, failed terminal events, and tier mismatches.

These are preparation requirements, not implemented flags. Do not pass a proposed `--service-tier` option to the current CLI until that support exists.

## Estimated inference cost

Source: the [GPT-6 Astra model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-astra.html), checked October 2, 2026. USD per million tokens for short-context US geographic inference:

| Tier | Input | Cache write | Cache read | Output |
| --- | ---: | ---: | ---: | ---: |
| Standard | $11.00 | $13.75 | $1.10 | $55.00 |
| Ultrafast | $66.00 | $82.50 | $6.60 | $330.00 |

Using nominal inputs of 1,000 and 10,000 tokens and assuming every request consumes its full 2,048-token output budget:

- Pilot, including warmups: approximately **$17**.
- Main comparison, including warmups: approximately **$255**.
- Both stages: approximately **$272**.

The calculation prices all input at the uncached input rate and all output, including reasoning, at the output rate. Actual prompt sizes, cache writes, cache reads, and failed-attempt usage can change the total. If all input were charged at the listed cache-write rate, the same nominal combined plan would be about $295. These are estimates, not an enforced spend cap or a complete AWS bill; client hosting and other infrastructure are additional.

Review the pilot's actual usage and streaming behavior before choosing a main-run budget. Keep any changed token limits or reasoning settings in a revised, separately identified configuration.

## Blog and repository deliverables

Save the final configuration, run schedule, raw attempt records, and a report that regenerates its tables and charts from those records. Publish the exact harness revision and source files alongside the findings.

The blog should show a compact Standard-versus-Ultrafast table with TTFT, visible throughput, full response time, completion counts, and estimated cost. Generate a figure from the saved data with sample counts and variability. Include the test date, client location, route, effort, actual token counts, and cache conditions.

Use wording such as "In our tests, under the following conditions..." once measurements exist. Keep the separately attributed OpenAI speed claim distinct from your measured result. A latency test alone does not establish unchanged answer quality or prove a universal speedup.
