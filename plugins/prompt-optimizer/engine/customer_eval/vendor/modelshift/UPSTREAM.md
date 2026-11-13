# ModelShift ingestion

Source: https://github.com/aws-samples/sample-amazon-bedrock-model-migration-confidence-evaluator

Commit: `f84088569820c37252cc614cbe21862810848734`. `ingest.py` and `schemas.py` are unmodified copies, reused for shape detection and response normalization. The surrounding customer_eval.log_import module adds strict input validation and preserves original message roles and request settings. No upstream scoring, pricing, transport or UI is imported.

License: MIT No Attribution; included in LICENSE.
