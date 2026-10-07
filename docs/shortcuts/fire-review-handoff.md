# Review bundle round trip — October 7

FIRE Review Evidence still contains the synthetic fixed prompt. Do not mistake that
prototype for live task input. The local preview for the original Siri/Qwen finding
is `.runtime/task-live-trial/apple-review-preview.md`; bundle is
`.runtime/task-live-trial/apple-review-bundle.json` (revision 7, submission 1).
Private preview/bundle files remain ignored; do not commit or share automatically.

After explicit choice to send this bundle to Apple Cloud, replace the prototype
prompt with the previewed bundle and request supported/unsupported claims and
corrections. Save/copy the exact returned text. Capture locally through stdin:

```sh
.venv/bin/python -m scripts.task_review_capture \
  --state "$HOME/Library/Application Support/FIRE/tasks" \
  --project rest_proxy \
  --bundle .runtime/task-live-trial/apple-review-bundle.json < assessment.txt
```

This imports unverified advisory text only. It does not accept/cancel/retry a task.
Changed task revision, edited bundle, invalid submission or replay rejects import;
prepare a new preview after any change. Content may include private source, even
though export excludes workspace and bearer tokens. Caller reviewer labels do not
prove model identity. GUI preview/input/capture actions still need wiring; manual
round trip is documented, not a claim of finished automated integration.
