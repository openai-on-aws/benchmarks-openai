# Record review of expected answers

Use this when a person has reviewed the expected answers or wants to do so.
Importing logs, accepting generated fixtures, or running an automated judge
does not establish human review. Historical model responses remain agreement
references unless the customer supplies an independent correctness reference.

Generate a review template after the selected cases and expected answers are
resolved. The template binds the review to the dataset, each input and each
expected answer using SHA256 hashes. Its outcomes start as `pending`; reviewer
identity, timestamp and attestation are unset. Inspect the input preview to see
the requests, expected answers and any existing review provenance.

A human reviewer can leave cases pending or remove entries for cases they did
not review. For completed review, they fill in:

```json
{
  "reviewer": {"name": "Actual reviewer name", "type": "human"},
  "reviewed_at": "2026-11-22T12:00:00-08:00",
  "attested": true
}
```

Each reviewed case retains its generated hashes and gets an `accepted` or
`rejected` outcome. Notes can explain a rejection. These fields are a person's
attestation, not authentication or proof of independent review. Do not fill
them with a simulated customer's name or mark an automated review as human.

Configure `label_review_file` with the completed file's path. Before preparing,
the helper verifies the dataset and per-case hashes. Accepted cases become
`human_reviewed`; pending and unlisted cases remain `unreviewed`. A partially
reviewed split remains `unreviewed`, with the reviewed cases listed separately.
Synthetic provenance remains synthetic even after an actual human review.

Resolve stale hashes, nonhuman attestations or rejected answers before
preparation. Repairing an answer or changing the selected input set needs a
new template and review. To run explicitly without human review, remove the
configured review file (`label_review_file: null`); retain unreviewed labeling
and describe the resulting limits. Do not edit hashes to reuse an old review.

Frozen case metadata and the customer context retain the reviewer, timestamp,
outcome and hashes. Those records travel with the evaluation results.
