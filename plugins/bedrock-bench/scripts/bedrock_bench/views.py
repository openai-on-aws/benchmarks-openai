"""Self-contained, offline HTML views shared by the library and replay."""

from __future__ import annotations

import json
from pathlib import Path

from .explorer import MAX_INLINE_BYTES

ASSETS = Path(__file__).resolve().parents[2] / "assets"


def embedded_json(data):
    """Escape JSON for a script element, including hostile saved trace strings."""
    return (json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))


def document(fragment):
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><title>Bedrock Bench</title>'
        '<style>body{margin:0;padding:24px;background:light-dark(#f7f7f8,#131415)}'
        '@media(max-width:600px){body{padding:10px}}</style></head><body>\n'
        + fragment + "\n</body></html>\n"
    )


def write_view(directory, name, data, fragment, *, inline=False):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    full = directory / f"{name}.html"
    full.write_text(document(fragment))
    if not inline:
        return full
    if len(fragment.encode("utf-8")) > MAX_INLINE_BYTES:
        raise ValueError(f"View exceeds the 1 MB inline limit; open {full} or use --format html. "
                         "For a smaller library, reduce --limit or --replay-limit.")
    path = directory / f"{name}.inline.html"
    path.write_text(fragment)
    return path
