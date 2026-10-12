"""CLI-facing validator for the human gold-standard JSONL contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from evaluation.benchmark.contract import (
    BenchmarkValidation,
    load_jsonl,
    validate_records,
)


def validate_jsonl(path: Path, *, trusted_identities: Mapping[str, Any] | None = None) -> BenchmarkValidation:
    """Load and validate one JSONL asset."""
    return validate_records(load_jsonl(path), trusted_identities=trusted_identities)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate human gold-standard benchmark JSONL.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--identities", type=Path, help="独立核验的身份登记 JSON")
    args = parser.parse_args()
    try:
        result = validate_jsonl(args.path, trusted_identities=json.loads(args.identities.read_text()) if args.identities else None)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "problems": [str(error)]}, ensure_ascii=False))
        return 2
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
