# Smart-glasses personal memory — standalone project direction

Accepted direction: October 4, 2026.

Explore a separate application that helps a person recall selected experiences
using evidence captured through smart glasses and a companion phone app. This
document is retained here as a planning reference; implementation belongs in a
separate project with its own repository, product decisions and acceptance gates.
The existing rest_proxy application continues to focus on trustworthy repository
evidence and coding-agent investigation tools.

## First step: Ray-Ban Meta capture integration

Investigate Ray-Ban Meta glasses as the wearable capture interface. Build a small
companion-app experiment using Meta's Wearables Device Access Toolkit, connected
to a dedicated personal-memory backend. Verify access on a specific supported
device and SDK version before committing to hardware or promising functionality.

The first technical milestone is an explicitly initiated capture reaching the
backend with its original media, capture timestamp, source/device identity and
user-selected retention scope. Then retrieve that capture in response to a simple
question and return its supporting evidence. Continuous recording is a separate
product decision, outside this initial experiment.

Meta's published toolkit documentation currently describes developer-preview
access; its iOS documentation marks synchronized camera-stream audio as
experimental and unavailable for production publishing. Record actual camera,
microphone, audio-output and background-session capabilities for the chosen SDK
and hardware rather than assuming built-in Meta AI capabilities are exposed to
third-party apps. Check SDK terms and distribution restrictions separately from
the licensing of any memory backend.

Sources checked October 4, 2026:

- [Ray-Ban Meta product lineup](https://www.ray-ban.com/usa/ray-ban-meta-ai-glasses)
- [Meta Wearables Device Access Toolkit](https://developers.meta.com/wearables/device-access-toolkit/)
- [Meta iOS toolkit and capability limitations](https://github.com/facebook/meta-wearables-dat-ios)
- [Meta Android toolkit](https://github.com/facebook/meta-wearables-dat-android)

## Memory architecture

Proposed flow:

```text
Glasses → companion phone app → selected media capture
        → transcription / OCR / visual observations
        → original evidence store + temporal graph + semantic retrieval
        → bounded evidence bundle → spoken or displayed answer
```

Store original observations separately from machine interpretations and
user-confirmed facts. Each interpretation should retain a reference to the
supporting media or transcript segment, capture time, processing/model version,
confidence when meaningful, and correction history. Unavailable originals must
be explicit. Use personal/session scopes appropriate to this application rather
than treating a person's life as a repository project namespace.

The graph could connect observations, objects, places, events, documents and
user-entered relationships. Relationships need temporal meaning: seeing an object
at a location establishes an observation at that time, not its current location.
Avoid silently merging similar objects or treating inferred identities as facts.

Example: “Where did I leave my keys?” may produce “Your keys were last recorded
on the kitchen counter at 4:12 PM,” with the supporting image. If the evidence
does not establish that they are the user's keys, disclose that uncertainty.

## Relationship to FIRE

Find, Integrate, Retrieve, Explain is a useful design pattern for this project:
capture observations, connect them, retrieve relevant evidence, and explain with
sources. Provenance, bounded context, correction, retention and storage-failure
handling are potentially reusable concepts.

This does not expand rest_proxy's implementation roadmap or imply that its graph,
retrieval or checkpoint code already supports personal memory. Evaluate reuse
behind explicit interfaces after the capture experiment. Keep personal media,
credentials, memory indexes and runtime services separate from repository tooling.
Inference-provider and storage choices remain open for the new project.

## Potential Alzheimer’s and dementia support

Explore memory assistance as a possible later use case, especially retrieving
misplaced objects, confirmed plans and familiar experiences. Assistive technology
already offers reminders and orientation support, but that does not establish
benefit for this proposed glasses-based system. This is not a treatment claim.

Design and evaluate with people living with dementia, caregivers and relevant
clinicians or occupational therapists. Assess ease of use, distress, successful
recall, incorrect answers and caregiver effort. Assistance must adapt to the
individual; prompts should be simple and controllable. A captured medicine bottle
does not prove medication was taken, and this experiment must not infer dosing or
replace supervision from visual evidence.

References:

- [Alzheimer’s Society: how technology can help](https://www.alzheimers.org.uk/get-support/living-with-dementia/how-technology-can-help)
- [Alzheimer’s Society: using technology in everyday life](https://www.alzheimers.org.uk/get-support/living-with-dementia/using-technology-everyday-life)

## Initial acceptance and next decisions

1. Verify supported capture and playback capabilities on the selected glasses,
   phone platform and SDK; record preview and production restrictions.
2. Demonstrate deliberate capture, backend delivery and retrieval with timestamped
   original evidence. Handle disconnects and missing captures explicitly.
3. Demonstrate “remember this / help me recall it” with a small object-location
   example. Distinguish historical observations from current-state assertions.
4. Establish consent, capture indication, pause controls, access boundaries and
   retention choices. Deletion must cover original media and derived transcripts,
   embeddings, graph records and summaries, with a defined backup policy.
5. Measure retrieval correctness, unsupported claims, response time and usability
   before expanding to continuous capture or dementia-specific evaluation.

Project name, repository location, target phone platform and hardware selection
remain undecided. No new repository, device integration, personal-data collection
or clinical evaluation has been started by this direction document.
