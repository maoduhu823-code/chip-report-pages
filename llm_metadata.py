"""
llm_metadata.py - extract best-effort LLM executor and token usage metadata.

The CLI output formats of Claude Code and Codex can evolve, so this parser is
intentionally tolerant: it reads JSON, JSONL, and regex-style token fields.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
from pathlib import Path
from typing import Any


INPUT_KEYS = {
    "input_tokens",
    "prompt_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
}
OUTPUT_KEYS = {"output_tokens", "completion_tokens"}
TOTAL_KEYS = {"total_tokens"}


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _read_jsonish(text: str) -> list[Any]:
    parsed: list[Any] = []
    stripped = text.strip()
    if not stripped:
        return parsed

    try:
        parsed.append(json.loads(stripped))
        return parsed
    except Exception:
        pass

    for line in stripped.splitlines():
        line = line.strip()
        if not line or not line.startswith(("{", "[")):
            continue
        try:
            parsed.append(json.loads(line))
        except Exception:
            continue
    return parsed


def _sum_key_values(objects: list[Any], keys: set[str]) -> int | None:
    total = 0
    found = False
    for obj in objects:
        for node in _walk(obj):
            for key, value in node.items():
                if key in keys and isinstance(value, (int, float)):
                    total += int(value)
                    found = True
    return total if found else None


def _regex_sum(text: str, keys: set[str]) -> int | None:
    total = 0
    found = False
    for key in keys:
        pattern = rf'"?{re.escape(key)}"?\s*[:=]\s*([0-9][0-9,]*)'
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            total += int(match.group(1).replace(",", ""))
            found = True
    return total if found else None


def extract_metadata(executor: str, log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    objects = _read_jsonish(text)

    input_tokens = _sum_key_values(objects, INPUT_KEYS)
    output_tokens = _sum_key_values(objects, OUTPUT_KEYS)
    total_tokens = _sum_key_values(objects, TOTAL_KEYS)

    if input_tokens is None:
        input_tokens = _regex_sum(text, INPUT_KEYS)
    if output_tokens is None:
        output_tokens = _regex_sum(text, OUTPUT_KEYS)
    if total_tokens is None:
        total_tokens = _regex_sum(text, TOTAL_KEYS)

    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    return {
        "ai_executor": executor,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "token_unit": "tokens",
        "token_source": str(log_path),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executor", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--out", default="output/llm_run_metadata.json")
    args = parser.parse_args()

    metadata = extract_metadata(args.executor, Path(args.log))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"LLM metadata written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
