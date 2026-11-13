#!/usr/bin/env python3
"""Progressive customer intake and the existing AgentCore evaluation workflow."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from customer_eval import project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'inspect', 'configure', 'prepare', 'status', 'run', 'results'])
    parser.add_argument('--project', required=True)
    parser.add_argument('--goal')
    parser.add_argument('--input', action='append')
    parser.add_argument('--sources', help='JSON array of {path, mapping?}; mappings may differ by log file')
    parser.add_argument('--decisions', help='JSON with only newly answered decisions; merged into saved intake')
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'start':
            value = project.start(args.project, args.goal or 'Evaluate my application and improve its prompt.')
        elif args.action == 'inspect':
            sources = project.read(args.sources) if args.sources else [{'path': x} for x in args.input or []]
            if not sources:
                raise ValueError('Provide --input files or a --sources mapping file')
            state = project.read(Path(args.project) / 'project.json')
            value = project.inspect(args.project, sources, prompt_location=state['decisions'].get('prompt_location'))
        elif args.action == 'configure':
            if not args.decisions:
                raise ValueError('Provide --decisions with the answers received so far')
            value = project.configure(args.project, project.read(args.decisions))
        elif args.action == 'prepare':
            value = project.prepare(args.project)
        elif args.action == 'status' and not (Path(args.project) / 'submission').exists():
            value = project.status(args.project)
        else:
            from customer_eval import project_cloud
            value = project_cloud.run(args.project, live=args.live) if args.action == 'run' else project_cloud.refresh(args.project)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'needs_attention', 'error_type': type(exc).__name__, 'message': str(exc),
            'recovery': 'Existing inputs and run receipts are retained. Correct the identified input or inspect the current job; do not resubmit a paid attempt blindly.'}, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
