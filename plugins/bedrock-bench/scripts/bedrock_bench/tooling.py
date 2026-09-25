"""Install upstream harnesses in separate, project-local Python environments."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .runners import redact
from .suites import SUITES, TOOL_REQUIREMENTS


def prepare(suite, tools_dir, *, execute=False):
    harness = SUITES[suite]["harness"]
    if harness == "built-in":
        return {"suite": suite, "installation_required": False}
    root = Path(tools_dir).resolve()
    environment = root / harness
    binaries = environment / ("Scripts" if os.name == "nt" else "bin")
    python = binaries / ("python.exe" if os.name == "nt" else "python")
    uv = shutil.which("uv")
    create = ([uv, "venv", "--python", sys.executable, str(environment)] if uv else
              [sys.executable, "-m", "venv", str(environment)])
    install = ([uv, "pip", "install", "--quiet", "--python", str(python)] if uv else
               [str(python), "-m", "pip", "install", "--quiet"])
    commands = ([] if python.is_file() else [create]) + [install + [TOOL_REQUIREMENTS[harness]]]
    result = {
        "suite": suite, "harness": harness, "requirement": TOOL_REQUIREMENTS[harness],
        "tools_dir": str(root), "commands": commands,
        "executable": str(binaries / (harness + (".exe" if os.name == "nt" else ""))),
        "execution": "Downloads and installs tools only; no model calls, containers, or AWS resources",
    }
    if not execute:
        return result
    if sys.version_info < (3, 12):
        raise ValueError("Harbor and aws-bench require Python 3.12 or newer; rerun prepare with Python 3.12+")
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, UV_CACHE_DIR=str(root / "uv-cache"))
    for command in commands:
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
        if completed.returncode:
            raise ValueError(f"Tool installation failed: {redact(completed.stderr)[-4000:]}")
    code = (
        "import importlib.metadata as m,json;"
        "print(json.dumps({p.metadata['Name']:p.version for p in m.distributions()},sort_keys=True))"
    )
    versions = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=True, timeout=30)
    result["installed_packages"] = json.loads(versions.stdout)
    result["prepared"] = True
    (environment / "bedrock-bench-tool.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
