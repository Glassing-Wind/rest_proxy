# Durable generation failures — October 7

26 checks passed: worker 13, registry 12, CLI 1. Focused Ruff and diff check pass.
Generation exceptions persist only timeout/generation_error category, generation
stage, time, attempt, observed revision and remote_termination=unknown. Exception
text and provider body excluded. Original exception still propagates to caller.

Best-effort recording uses current claim token plus expected revision and claimed
status. Expiry alone allows a current worker to report failure; it does not extend
lease or authorize source access. Cancellation/reclaim/revision change invalidates
recording. Failure recording never retries or changes task status. Payload/record
caps can reject persistence; this must not hide the original generation exception.
External coroutine cancellation and errors before generation or after returned
finding validation are not captured by this slice; explicit recovery still applies.

Fixture proves retained checkpoint, sanitized timeout and reopen; concurrent caller
cancellation preserves cancelled state without failure mutation. No live provider
call or task mutation. Prior actual Siri timeout remains in dated evidence only.
