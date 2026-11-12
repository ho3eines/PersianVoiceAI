#!/usr/bin/env python3
"""Validate Persian evaluation kit.

Usage:
    python validate_eval.py persian_eval_v0_1.jsonl score_template.csv

The script checks that the JSONL file contains exactly 12 valid scenarios,
each with a unique ID, proper category, and non-empty text.
It then creates/updates score_template.csv with a header and rows for each scenario.
"""

import sys
import json
from pathlib import Path

EXPECTED_CATEGORIES = {
    "greeting", "numbers", "datetime", "english_terms",
    "names", "zwnj", "arabic_chars", "long_sentence",
    "spoken", "question", "command"
}

def load_scenarios(jsonl_path: Path):
    scenarios = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Line {line_num}: invalid JSON - {e}")
            # Validate required fields
            for field in ("id", "category", "text"):
                if field not in obj or not isinstance(obj[field], str) or not obj[field].strip():
                    raise ValueError(f"Line {line_num}: missing or empty '{field}'")
            if obj["id"] in [s["id"] for s in scenarios]:
                raise ValueError(f"Line {line_num}: duplicate id '{obj['id']}'")
            if obj["category"] not in EXPECTED_CATEGORIES:
                raise ValueError(f"Line {line_num}: unknown category '{obj['category']}'")
            scenarios.append(obj)
    return scenarios

def main():
    if len(sys.argv) != 3:
        print("Usage: python validate_eval.py <scenarios.jsonl> <score_template.csv>")
        sys.exit(1)

    jsonl_path = Path(sys.argv[1])
    csv_path = Path(sys.argv[2])

    if not jsonl_path.is_file():
        print(f"Error: {jsonl_path} not found")
        sys.exit(1)

    try:
        scenarios = load_scenarios(jsonl_path)
    except Exception as e:
        print(f"Error loading scenarios: {e}")
        sys.exit(1)

    if len(scenarios) != 12:
        print(f"Error: expected 12 scenarios, found {len(scenarios)}")
        sys.exit(1)

    # Write score_template.csv
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        f.write("id,category,score,critical_fail\n")
        for s in scenarios:
            f.write(f"{s['id']},{s['category']},,,0\n")  # empty score and critical_fail=0

    print("VALID: 12 cases")
    # Print category summary
    cat_counts = {}
    for s in scenarios:
        cat = s["category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        print(f"  {cat}: {s['id']}")

if __name__ == "__main__":
    main()