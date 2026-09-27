#!/usr/bin/env python3
"""Portable offline skill entrypoint; independent of the repository's CLI."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from bedrock_bench.diagnosis import write_diagnosis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="Saved run JSON files or run directories")
    parser.add_argument("--out", required=True, type=Path, help="Directory with no existing diagnosis outputs")
    parser.add_argument("--limit", default=100, type=int, help="Evidence details, 0–1000; all counts are retained")
    arguments = parser.parse_args()
    try:
        result = write_diagnosis(arguments.sources, arguments.out, limit=arguments.limit)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Diagnosis could not be written: {exc}\n")
    print(result)


if __name__ == "__main__":
    main()
