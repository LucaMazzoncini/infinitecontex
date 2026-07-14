"""Explicitly regenerate token-estimator goldens from a local tokenizer asset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer-json", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--generated-at", required=True, help="ISO-8601 timestamp supplied explicitly")
    args = parser.parse_args()

    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise SystemExit("Install the optional `tokenizers` package only for explicit regeneration.") from exc

    corpus: dict[str, Any] = json.loads(args.corpus.read_text(encoding="utf-8"))
    tokenizer = Tokenizer.from_file(str(args.tokenizer_json))
    counts = {
        sample["id"]: len(tokenizer.encode(_normalize(sample["text"]), add_special_tokens=False).ids)
        for sample in corpus["samples"]
    }
    generated_at = datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
    if generated_at.tzinfo is None:
        raise SystemExit("--generated-at must include a timezone")
    payload = {
        "schema_version": 1,
        "reference": {
            "kind": "model-family tokenizer reference; not exact for every supported model",
            "implementation": "Hugging Face tokenizers.Tokenizer.encode(add_special_tokens=False)",
            "repository": args.repository,
            "revision": args.revision,
            "tokenizer_asset": "tokenizer.json",
            "tokenizer_sha256": hashlib.sha256(args.tokenizer_json.read_bytes()).hexdigest(),
            "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "normalization": "CRLF and lone CR replaced with LF before encoding; no trimming or Unicode normalization",
            "command": (
                "uv run --with tokenizers scripts/regenerate_token_estimator_goldens.py "
                "--tokenizer-json <local-tokenizer.json> "
                "--corpus tests/fixtures/token_estimator/corpus.json "
                "--output tests/golden/token_estimator_qwen2_5_coder.json "
                f"--repository {args.repository} --revision {args.revision} "
                f"--generated-at {args.generated_at}"
            ),
        },
        "counts": counts,
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


if __name__ == "__main__":
    main()
