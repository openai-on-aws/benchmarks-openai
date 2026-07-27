<!-- FROZEN ARTIFACT — DeepSearchQA autorater prompt, reproduced verbatim from
     "DeepSearchQA: Bridging the Comprehensiveness Gap for Deep Research Agents"
     (arXiv:2601.20975), Appendix A. Judge model: gemini-2.5-flash.
     DO NOT EDIT: any change makes results incomparable with the public
     leaderboard and with prior runs. judge_deepsearch.py loads the template
     between the TEMPLATE markers and fills {prompt}, {prompt_type}, {answer},
     {response}. -->

<!-- TEMPLATE:START -->
Your task is to evaluate whether a given "AI Response" for a specific "User Prompt" arrived at the correct answer.

Answer Correctness Task

- Purpose: Assess whether the AI response provides the correct answer(s) based on the provided "Correct Answer" and "Prompt Type".

- Process:
  - Identify the "Prompt Type": "{prompt_type}".
  - Refer to the "Correct Answer": "{answer}".
  - Based on the "Prompt Type", determine if the "AI Response" contains the expected answer(s).
    - 'Single Answer': Check if the response provides the answer that addresses the user's question. It does not have to match the exact wording of the provided answer.
    - 'Set Answer': Check if the response includes *each* item from the provided ground truth answers. The order might not matter unless specified otherwise. The response might include more answers than the list. Determine the correctness *only* based on the list first and then check if the response includes answers not in the list.
  - Explanation: Provide a brief explanation justifying your assessment of answer correctness, referencing specific parts of the AI response and the correct answer.
  - Correctness Details: Provide a dictionary, one key for each expected answer part, and value is a boolean indicating whether each expected answer part was found.
  - Excessive Answers: Provide a list of strings, each indicating an excessive answer part. If the response provides answers that are **not** in the "Correct Answer" list, add these answers as excessive answers. Return an empty list when there's no excessive answers in the response.

Output Format:

Your evaluation *must* be structured as a nested JSON dictionary with the following top-level keys: "Answer Correctness". Please return NULL if any of "Prompt", "AI Response" or "Correct Answer" is empty.

The value for "Answer Correctness" should be a dictionary containing "Explanation" (a string), "Correctness Details" (a dictionary where each key is the expected correct answer, and the value is a boolean indicating whether the response contains the correct answer), and "Excessive Answers" (a list of strings indicating the excessive answers).

Make sure you return a valid JSON string. Pay special attention to quotes, commas and special characters in the JSON string. Make sure to escape all special characters and quotes in the JSON string.

User Prompt:
{prompt}

AI Response:
{response}
<!-- TEMPLATE:END -->

## Metric computation (from the paper, §Evaluation)

Per prompt i, with S_i = submitted answers (correct hits + excessive) and
G_i = ground-truth answers:

- Precision  P_i = |S_i ∩ G_i| / |S_i|   = true_count / (true_count + excessive_count)
- Recall     R_i = |S_i ∩ G_i| / |G_i|   = true_count / len(correctness_details)
- F1_i       = 2·P_i·R_i / (P_i + R_i)   (0 when P+R = 0)

Single Answer prompts reduce to binary exact-match (F1 ∈ {0, 1}).
Benchmark scores = per-prompt metrics averaged over the evaluation set.
