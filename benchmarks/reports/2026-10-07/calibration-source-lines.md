# Fixture citation inspection — October 7

Four calibration tests and three export regressions passed; focused Ruff passed.
The resolver rejects absent path/line, bool range values and ranges above 100 lines.
Fixture intake line 4 contains decoded goal validation; line 5 contains registry
creation. Both are valid ranges, so existence alone cannot catch an incorrect claim.
Human inspection of returned source remains required. This is a fixture helper,
not production citation semantic validation or a measured improvement in accuracy.

Use `citation_excerpt(prompt, path, start, end)` from
`scripts.task_review_calibration` after producing the appropriate variant prompt.
Never resolve missing-definition citations against the full variant.

The version-2 fixture preserves original fixture and trial evidence and splits
supplied read_source argument from guaranteed execution. Generate v2 input with:

```sh
.venv/bin/python -m scripts.task_review_calibration \
 --fixture benchmarks/reports/2026-10-07/advisory-review-calibration-fixture-v2.json \
 --variant full
```

Run missing-definition separately. No v2 model responses or scores recorded yet.
Task/model/service state unchanged; hosted CI for 7ebc8e4 passed eight checks.
