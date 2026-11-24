"""Import captured text requests for the existing evaluation/optimizer runner."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from customer_eval.log_import import import_logs, strict_json, write_import


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', required=True, help='JSON, JSONL or gzip log exports')
    parser.add_argument('--mapping', help='JSON mapping for custom application logs')
    parser.add_argument('--prompt-location', help='instructions or zero-based instruction-message index; default first instruction')
    parser.add_argument('--reference-mode', choices=('standard', 'source_judge'), default='standard',
                        help='source_judge imports source-only requests without inventing expected answers')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    location = args.prompt_location
    if location is not None and location != 'instructions':
        location = int(location)
    mapping = strict_json(Path(args.mapping).read_text()) if args.mapping else None
    cases, report = import_logs(args.input, mapping=mapping, prompt_location=location, reference_mode=args.reference_mode)
    report = write_import(cases, report, args.output)
    print(json.dumps({k: v for k, v in report.items() if k not in ('sources', 'excluded_records')}, indent=2))
    if not cases:
        raise SystemExit('No usable requests; inspect import-report.json')


if __name__ == '__main__':
    main()
