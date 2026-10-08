# Internal gateway event contract — October 7

Two offline tests exercise canonical hash mismatch, detached payload, exact fields,
unknown action type, invalid version/timestamp/identifier and UTF-8 byte budget.
Focused lint passes. No journal, route, crash, throughput or replay acceptance yet.

The full supplied architecture study is retained in docs/research with its original
proposal status and source URLs. Those sources were not independently verified in
this slice. Contract is internal v1; no CloudEvents or authenticated producer claim.
Caller must redact before validation/persistence; arbitrary nested payload may carry
secrets, so this validator alone is not a redaction/security boundary. New prototype
is not wired to live traffic. License and existing inference behavior unchanged.
