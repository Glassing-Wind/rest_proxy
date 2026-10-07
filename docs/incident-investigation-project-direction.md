# Authorized incident investigation — separate exploratory direction

Research recorded October 4, 2026. This is a possible standalone application,
separate from rest_proxy's coding-agent tooling and the
[smart-glasses personal-memory project](smart-glasses-project-direction.md).
No connector, customer access, repository or operational data collection has been
created. Product name and implementation decisions remain open.

## Proposed role

Explore whether a provenance-preserving graph and retrieval service could help
authorized investigators assemble incident timelines from permitted camera
observations, incident records and investigator notes. Flock Safety is a potential
connector, not a selected or verified integration.

Flock advertises API integrations and license-plate-reader observations including
plates and vehicle characteristics. That supports investigating a connector;
it does not establish which endpoints or data a particular customer can access.
Verify those capabilities against the customer's authorized developer access.

An observation should retain its original source ID, capture time and location,
permitted evidence reference, access scope and retention/preservation status.
Keep extracted plate text and candidate vehicle matches distinct from confirmed
facts. A plate observation does not establish who was driving, and a possible
match should remain an investigative lead with supporting evidence and uncertainty.

Potential output: an incident-scoped timeline whose entries link to authorized
source evidence, with gaps and conflicting observations visible. Human review
should precede consequential conclusions; generated associations must not silently
become verified identity or guilt claims.

## Verified vendor constraints

Flock's published API and Integrations Terms, last updated October 13, 2025,
limit implementation use to bona fide law-enforcement purposes and define
integration data as requested at a Flock customer's express instruction.
They restrict access to documented API functions, bulk extraction/database-like
access, and data sharing. They also prohibit use of the implementation for
machine-learning model development or evaluation.

These terms make a general personal-memory ingestion feed an unsupported
assumption. Whether a proposed retrieval, embedding, inference or evaluation
workflow is permitted needs explicit clarification with Flock and the authorized
customer before building that connector. This document records the published
constraints; it does not establish contractual approval or interpret every proposed
AI operation as either permitted or prohibited.

Flock's privacy materials describe justified law-enforcement searches and audit
trails. An integration would need to preserve agency/case access boundaries,
record who requested what and why, and respect applicable source retention,
preservation and sharing controls. Derived graph and vector records must not
silently bypass those source restrictions. Exact policies depend on the authorized
deployment and must be established before operational use.

## Framework candidates for synthetic evaluation

Graphiti's temporal model may help separate when an observation occurred, when it
was recorded and when a later correction became known. Evaluate historical queries
and source-backed associations on synthetic incident timelines. Generated links
must remain distinguishable from original observations and confirmed findings.
LightRAG/Fast GraphRAG are document-retrieval candidates; LangGraph could coordinate
bounded search and human-review steps. These are candidates, not adopted tools.

Keep case/agency scope, access controls and source retention in application-owned
contracts. A framework's capabilities do not establish vendor permission for data
ingestion, embedding, inference or evaluation. Existing Flock authorization gates
continue to apply. See the separate
[framework research and compatibility limits](evidence-routing-framework-research.md).

## First investigation and acceptance gates

1. Identify an authorized customer, concrete incident workflow, supported API
   functions and vendor-approved data/AI uses. Do not assume network-wide access.
2. Prototype an incident timeline using synthetic observations before accessing
   operational data. Keep personal-memory and repository indexes separate.
3. Check provenance, timestamps, ambiguous matches, corrections and conflicting
   observations; require source evidence for each generated association.
4. Verify case/agency isolation, authorization, audit logging, retention/deletion
   and explicitly authorized preservation/export behavior.
5. Measure timeline/citation correctness, false associations and investigator
   effort before claiming improved investigative outcomes.

Reusable concepts include FIRE's bounded retrieval and the proposed WET evidence
trail. Reuse of actual components requires explicit interfaces and independent
deployment acceptance; no change to the existing coding-tooling roadmap is implied.

## Primary sources checked October 4, 2026

- [Flock license-plate readers and integrations](https://www.flocksafety.com/products/license-plate-readers)
- [API and Integrations Terms](https://www.flocksafety.com/legal/api-integration-terms)
- [API developer hub](https://docs.flocksafety.com/)
- [Privacy policy](https://www.flocksafety.com/legal/privacy-policy)
- [Law-enforcement access and safeguards](https://www.flocksafety.com/trust/law-enforcement-access)
