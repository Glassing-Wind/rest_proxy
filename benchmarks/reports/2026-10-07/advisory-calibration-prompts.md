# Isolated calibration prompts — October 7

Seven offline tests passed; focused Ruff passed. Prompt generator performs no
network calls or task operations. Expected answers stay in the fixture for manual
grading and are excluded from emitted prompts. No model accuracy measured yet.

Generate each variant (replace full with missing-definition for the second):

```sh
.venv/bin/python -m scripts.task_review_calibration \
  --fixture benchmarks/reports/2026-10-07/advisory-review-calibration-fixture.json \
  --variant full
```

Use the emitted JSON as model input. Retain returned text, visible provider label,
variant, instruction version, and actual usage if supplied. Grade each of four
claims manually against the fixture; missing registry definition must yield
unresolved project/type interpretation. Count false positives and omissions;
format compliance alone is not a semantic pass. Neither response approves tasks.
