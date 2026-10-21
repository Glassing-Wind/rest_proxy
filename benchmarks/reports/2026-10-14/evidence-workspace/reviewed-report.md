# A source-backed investigation

Case: browser-restored · Revision: 4

This report contains reviewed historical statements. Review labels are self-reported; acceptance does not authenticate a source, identity, payment or legal conclusion.

Coverage declaration: partial. Fictional code, records and images. One listed record is missing; another expected source is unlisted.
Unavailable sources: 1; unlisted expected sources: 1.
Unreviewed relationships: 2; rejected relationships: 0.

## Reviewed findings

### import-writes

The importer source references store.write after parse\(raw\).

Evidence type: observation; review: accepted.
Review reason: The cited three-line excerpt directly shows the write reference; no runtime execution claim is made.

Supporting source references:
- import-source: lines 1–3; supplied-copy check: matches-supplied-bytes; 2026-10-14T07:41:53.969750+00:00; revision 4; compared 2a11f49168b8616d8a351661e8dd73a9623e5387e6925ecc617e2bd0bfa005f2.

### controller-hypothesis

Contact alpha claimed control in the message; wallet ownership remains unverified.

Evidence type: inference; review: amended.
Review reason: The excerpt supports a claim of control only. It supplies no verified wallet ownership or identity evidence.

Supporting source references:
- message-record: lines 2–3; supplied-copy check: not-checked; not checked.

## Exhibit index

| Source | Kind | SHA256 | Availability | Supplied-copy check | Last operator check |
|---|---|---|---|---|---|
| import-source: Synthetic importer source | provided-code | 2a11f49168b8616d8a351661e8dd73a9623e5387e6925ecc617e2bd0bfa005f2 | stored | matches-supplied-bytes | 2026-10-14T07:41:53.969750+00:00; revision 4; compared 2a11f49168b8616d8a351661e8dd73a9623e5387e6925ecc617e2bd0bfa005f2 |
| transfer-record: Fictional transfer record | synthetic-record | 2a536654f403ebbb5192382b2e371e7395fc63b52e781f4fc434699297589f16 | stored | not-checked | not checked |
| message-record: Fictional conversation excerpt | synthetic-message | ddba343c70f8caa0583eeceac19e201af297d56a2e962fb93d46316e166866db | stored | not-checked | not checked |
| missing-confirmation: Delivery confirmation not acquired | missing-record | unknown | unavailable | not-checked | not checked |
| exhibit-image: Synthetic image region | synthetic-image | 16f6435d7ac46b4579dfa3630ddd7585a7494f7ac2fe955a976167f3a9a582dc | stored | not-checked | not checked |

Original bytes and retired review statements are excluded from this report. The separate recovery archive contains all originals and history.
Hashes establish snapshot consistency. Current-copy checks compare operator-supplied bytes, not an independently monitored live source.
Review this report for sensitive information before sharing it.
