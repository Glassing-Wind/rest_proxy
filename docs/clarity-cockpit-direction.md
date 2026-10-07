# CLARITY and the personal assistant cockpit — exploratory direction

Direction recorded October 5, 2026. CLARITY is a proposed name, not an acronym
with an agreed expansion. The aim is to help people understand events, recognize
unfair treatment, find support and improve everyday life through accessible,
source-backed information. This is a public-interest direction independent of
the authorized agency-camera integration.

## Three applications, shared contracts

| Application | Inputs and purpose | Boundary |
| --- | --- | --- |
| Repository FIRE | Source files and coding-task evidence. | Existing rest_proxy implementation roadmap. |
| Personal assistant | User-selected experiences, documents and observations; recall, planning and relevant spoken help. | Separate personal scopes and companion application. |
| CLARITY | Voluntary contributions, public records and news; evidence-based civic investigation and support. | Separate publication, contributor and audience permissions. |

The existing proxy/context service can be evaluated as a shared foundation for a
cockpit that shows agent activity, evidence, queued work and controls. Its existing
coding tools do not establish personal-memory, audio, continuous processing or
public-network support. Preserve the standalone
[smart-glasses direction](smart-glasses-project-direction.md) and
[incident-investigation direction](incident-investigation-project-direction.md).
Reuse bounded context, provenance and storage-owner interfaces after independent
acceptance; do not merge their data into repository indexes.


## Relationships, direction, team, contacts, gates and keys

Direction added October 6, 2026. These concepts describe a consent-aware relationship
map and the controls around it, not a claim that a social graph or glasses app is
already implemented.

| Concept | Proposed role | Required distinction |
| --- | --- | --- |
| Relationship | Link people, events, places, claims and originals with time and source. | A reported or inferred connection is not a verified identity or fact. |
| Direction | User-stated goals and interests determine relevance and what help is wanted. | The assistant must not infer life priorities from passive capture. |
| Team | Trusted participants contribute evidence and coordinate useful work. | Membership, task role and access permissions are explicit and separate. |
| Contacts / Facebook contacts | User-entered contacts, invitations and permitted imports can seed collaboration. | A contact is not automatically a participant or permission to access their information. |
| Gates | Policy checks control capture, retrieval, sharing, notification and actions. | Check purpose, audience, freshness and user approval at the action boundary. |
| Keys | Identities and credentials grant bounded capabilities with expiry/revocation. | An authentication credential is not proof of trust, consent or evidentiary truth. |
| Smart glasses | An optional capture/audio interface for selected observations and assistance. | SDK/device access does not grant access to a person's social contacts. |

Start with synthetic people/events and manually entered contacts. Every relationship
should carry source, when it applied, whether observed/reported/inferred/confirmed,
and who may view or correct it. Invitations are voluntary; never use proximity or
face similarity to turn an observation into an identified person.

A proposed gate sequence is: permitted input → scope/purpose check → bounded
retrieval → evidence review → action/disclosure check → optional user confirmation.
Keys need scoped access, expiration and revocation; they are not stored in evidence
or exposed in transcripts. Private, team and public scopes stay separate. These
are future product requirements, not implemented team authorization in rest_proxy.

Facebook connectivity is an unimplemented candidate. Verify current supported APIs,
permissions and terms before selecting an import method. Do not assume access to
an entire friend list, messages, private profiles or contact network, and do not
make the first prototype or income plan depend on such access. User-controlled
manual entries and voluntary invitations are sufficient for the first model.

Income is now an explicit constraint. The recommended near-term commercial track is
[a supervised FIRE investigation pilot](commercial-direction.md), while CLARITY,
personal memory and smart glasses remain separate longer-term directions. Useful
paid delivery/support could fund development; selling private contact graphs is
outside the proposed operating model. No revenue or customer demand is established.

## Personal assistant loop

```text
Permitted inputs → original evidence + time/source metadata
                 → transcription/OCR/observations → temporal memory
                 → bounded retrieval → local worker → evidence checks
                 → relevance/notification decision → audio or cockpit
```

Start with deliberate capture and answers on request. Then evaluate background
processing of explicitly enrolled inputs and proactive notifications. Continuous
processing does not imply continuous recording or continuous talking. Speech
recognition, speech synthesis and device audio access are separate integrations;
do not assume the selected text model or glasses SDK supplies them.

Proactive speech needs user-selected topics, quiet hours, urgency thresholds,
deduplication, expiry and a pause control. A notification should explain its source
and why it is timely. Measure useful interventions, false alerts, interruptions,
response latency and battery/resource costs. Happiness and quality of life are
user-defined outcomes to evaluate, not attributes a model can reliably infer from
passive surveillance.

## A voluntary people's evidence network

CLARITY should make civic evidence usable without depending on police camera
feeds. Contributors choose individual items to share, with explicit audience,
location precision and retention choices. Personal capture stays private unless
its owner selects an item for publication. Shared originals need controlled access,
redaction, correction and withdrawal propagation into derived indexes and summaries.

Compare specific claims across sources: what is asserted, original documents or
media, event time versus publication time, corroboration, contradictions and
missing evidence. Syndicated or copied reports are not independent corroboration.
Keep observations, allegations, model interpretations and confirmed findings
distinct. Show framing differences and uncertainty rather than presenting an
automatic universal "spin score" or claiming access to hidden truth.

Helping people facing unfair treatment should start with volunteered reports and
consent-based connections to support. Do not infer guilt, vulnerable status or
identity from proximity, images or graph associations. The contribution design
must account for bystanders, harassment, fabricated submissions and disclosure of
sensitive locations. These are product requirements for this public network.

First prototype: synthetic submissions describing one event, two conflicting news
accounts and one primary record. Produce a cited timeline, identify unresolved
claims, demonstrate correction/withdrawal, and connect an opt-in request for help
to a user-approved resource. No operational collection or publishing is enabled.

## Local workers and supervision

Qwen through LM Studio/Splash is a candidate for bounded routine work: extract
structured observations, compare supplied claims, propose search queries, summarize
with citations and draft limited code changes. Keep tools and data scoped to each
task. Validate schemas, citations and test results independently; worker confidence
alone is not a reliable escalation signal.

A supervisor can plan tasks, review compact worker receipts, investigate conflicts
and handle difficult decisions. Receipts should include task/input identity,
model/runtime, evidence references, output, validation, omissions and resource use.
Escalation needs explicit rules and limits on turns, tool calls, input/output tokens
and elapsed time. Sharing a compact receipt with a cloud supervisor must follow
the scope's disclosure policy; local processing alone does not make cloud review
private.

The current proxy selects a configured inference provider. It does not implement
automatic local/cloud task delegation or connect this Codex chat as a background
supervisor. An API-backed supervisor and a user-initiated Codex review are distinct
deployment choices. Measure cloud tokens including review, retries and rework;
local generation still consumes compute and context. Savings remain unproven.

## Named software and present evidence

- **Splash:** an inference engine specialized for supported Qwen models on Apple
  Silicon. LM Studio documents its experimental backend; Inco documents standalone
  compatible HTTP APIs. Integration still needs generation, streaming and tool-call
  acceptance through our proxy. [LM Studio announcement](https://lmstudio.ai/blog/splash-engine),
  [Inco engine/API reference](https://inco.ai/blog/splash/).
- **Harmony:** the structured conversation format used by OpenAI `gpt-oss`, not a
  separate model or voice assistant. Let a compatible inference server handle its
  rendering. Splash's cited launch support is Qwen, not proof of `gpt-oss` support.
  [Official OpenAI format reference](https://developers.openai.com/cookbook/articles/openai-harmony).
- **Shadowrocket:** a device-side rule-based network proxy utility. It may help
  route or inspect connections; it supplies neither the agent router nor evidence
  memory. Our inference HTTP proxy does not become a generic network tunnel merely
  by sharing the word proxy. It is optional infrastructure, not a cockpit dependency.
  [Developer's App Store listing](https://apps.apple.com/us/app/shadowrocket/id932747118).

A read-only check on October 5 found `qwen3.6-35b-a3b-splash` in the local native
model list with no loaded instances; the Jina code embedding model had one loaded
instance. This establishes discovery only. No model was loaded or inference run
for this direction update. Preserve the accepted embedding identity when testing
the new generative model; replacing embeddings requires a separately accepted index.

Subsequent October 5 execution: the
[bounded Qwen trial](../benchmarks/reports/2026-10-05/qwen-worker-trial.md) completed
one repository investigation and patch proposal with supervisor review and tests.
It used direct local HTTP, not automatic delegation or personal data; a failed
truncated attempt is retained. Current delivery gaps remain in the
[five-priority status](five-priority-status.md).
