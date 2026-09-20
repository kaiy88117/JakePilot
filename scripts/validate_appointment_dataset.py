"""Validate appointment-model JSONL before training or evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from appointment_training import DatasetValidationError, validate_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--formal", action="store_true")
    args = parser.parse_args()

    try:
        manifest = validate_dataset(args.input, formal=args.formal)
    except DatasetValidationError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {"valid": True, **manifest.model_dump(mode="json")},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
