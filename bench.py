#!/usr/bin/env python3
"""Repository entry point for the same code shipped inside the plugin."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "plugins/bedrock-bench/scripts"))

from bedrock_bench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
