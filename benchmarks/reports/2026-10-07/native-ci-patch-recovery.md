# Native embedded CI patch recovery — October 7, 2026

User supplied a GitHub 403 publication failure from another conversation and asked
about the patch. Downloads contained no .patch/.diff file. The message was found
in the ChatGPT conversation titled Compare PR Merge Order and its cloud execution
record (scratch/19b9ba3629f6). The cloud record showed a proposed embedded-native
job in .github/workflows/ci.yaml; the integration update failed. No command from that
conversation was treated as authorization to push or merge here.

Recovered the job definition from recorded workflow output and integrated it into
current local workflow. This is a reconstructed equivalent job, not the original
cloud git-am patch. Uses Ubuntu, existing modified ts-pack wheel build/download,
Python 3.14, watcher disabled, required fork-wheel installation, ladybug 0.21.2,
LanceDB 0.39.0 and pyarrow 25.0.1, pip check, then native Ladybug/outline/repository
suites. No inference/provider lifecycle changes.

Current local validation: YAML parsed, test paths/dependencies checked, diff check
passed; native Ladybug 4 tests, outline 4 and repository 23 tests passed (31 total).
Installed engine versions matched job pins. Existing deprecation/async timing logs
appeared without failures. This macOS local run is not fresh Ubuntu hosted acceptance.
No fresh CI dependency installation or hosted checks performed. The original cloud
18-repository-test claim is historical; current local suite contains 23 tests.

No GitHub update, push, retarget or merge attempted. The prior reported 403 is not a
new test of this checkout's credentials. PR status/merge ordering was not reverified
in this milestone. A locally generated format-patch is placed in Downloads after
commit so the user can inspect/share it; do not apply it again to this checkout.
Hosted checks and PR review are required before any merge decision.
