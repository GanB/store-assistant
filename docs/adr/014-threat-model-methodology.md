# ADR-014: Threat-model methodology and maintenance

## Status

Accepted.

## Context

Threat models go stale. A document written once, reviewed once, and filed away loses operational value within a release cycle — new tools, new data flows, new model versions, and new compliance signals all shift the surface. For this system specifically, a threat model that does not move with the code base is worse than no threat model: it lulls a reviewer into believing a control exists that has since been removed.

This ADR establishes how [docs/THREAT_MODEL.md](../THREAT_MODEL.md) was constructed, how it stays current, and how it ties to the eval harness ([ADR-012](012-eval-harness.md)) so adversarial coverage is enforced by CI rather than by good intentions.

## Decision

### Methodology — two frameworks, deliberately

- **STRIDE per component** for breadth. Section 2 of THREAT_MODEL.md walks each architectural component (agent, tools, DB, tracing, UI, eval harness) and enumerates the six STRIDE categories: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege. Six categories × six components = a structured floor on coverage.
- **OWASP LLM Top 10 alignment** for depth on novel threats. Section 3 covers attack shapes that don't exist in non-LLM systems: prompt injection (direct, indirect via tool output, indirect via DB content), jailbreaks (role-play, hypothetical), training-data extraction, hallucinated tool calls and arguments, output manipulation, cost amplification, context-window poisoning, tool-result poisoning. These are not adequately captured by STRIDE alone; the LLM-specific framing makes them visible.
- The two frameworks together are the floor, not the ceiling. Threats that don't fit either (e.g., compliance-driven threats like "model output triggers UDAAP exposure") are added to Section 5 of THREAT_MODEL.md with their own framing.

### Cadence

Triggered review:

- **Every architecture change.** New service, new database, new external dependency, new tracing backend.
- **Every new tool added to the agent registry.** Each tool widens the trust surface; each one needs explicit threat analysis.
- **Every new data type stored.** A new column on a table, a new entity, a new cache. Each one needs a sensitivity classification (Section 1.3 of THREAT_MODEL.md) and a corresponding STRIDE analysis on the components that touch it.
- **Every model swap.** A new LLM model version may shift the prompt-injection / jailbreak surface. Re-run the adversarial eval set; reflect any new threats in Section 3.
- **Every compliance-environment change.** New jurisdiction (e.g., expanding to a new state's PII regime); new regulator guidance.

Calendar review: at minimum quarterly, even if no triggered review has fired. The intent is to keep the document a living artifact, not a one-time deliverable.

### Linkage to evals

Every threat in Section 3 of THREAT_MODEL.md must, by Section 4 of the same document, be in one of two states:

1. **Covered (or partially covered) by an eval trace** in `evals/dataset/traces.jsonl` (category=adversarial). The trace ID (e.g., `EV017`) is named in Section 4's coverage table.
2. **Explicitly listed as "no automated coverage"** with a rationale and (where appropriate) a TODO for a future eval.

T406 in `tests/unit/test_documentation.py` enforces that every `TM-LLM-NNN` ID in Section 3 has a row in Section 4. The build fails if a new threat is added without that linkage. This is the mechanism that turns a documentation artifact into a CI-enforced contract.

### Linkage to ADRs

When a mitigation moves from "deferred" to "present" — a control gets implemented — the change requires:

1. An ADR documenting the design choice (or an update to an existing ADR).
2. An update to the relevant Section 2 / Section 3 row in THREAT_MODEL.md, moving the mitigation from the "Deferred mitigation" column to the "Present mitigation" column.
3. An update to Section 6's traceability matrix.

Conversely, when a control is removed (rare; should be intentional), the same three-step update happens in reverse with a clearly documented risk acceptance.

### Ownership

- **Document owner:** the repository maintainer with primary responsibility (currently the same author as this ADR; transitions to a security review board for any production deployment).
- **Reviewers required for changes:**
  - For changes to Section 2 (STRIDE per component): a peer engineer.
  - For changes to Section 3 (LLM-specific threats): a peer engineer plus, in production, an applied-AI security reviewer.
  - For changes to Section 5 (compliance posture): a compliance reviewer in production deployments. The current demo does not have such a reviewer; the document is calibrated to "posture awareness, not legal advice."
- **CODEOWNERS placeholder:** `/docs/THREAT_MODEL.md @maintainer` (and the new ADRs). When the project transitions to a real team, this becomes a security-review-board entry.

### PR-template integration

The PR template (currently the implicit pattern in commit messages; explicit in any PR-driven workflow) carries a checkbox: **"Does this PR change the threat surface?"** If yes, the PR description must point to the THREAT_MODEL.md or eval-dataset diff that captures the change. Reviewers reject PRs that change the surface without the corresponding doc update.

## Rationale

STRIDE is broad and well-known; it produces a structured floor on coverage and is recognisable to a security reviewer who reads it cold. OWASP LLM Top 10 is the catalogue of what makes LLM-driven systems different. Neither alone covers the full surface — STRIDE's "Information disclosure" line item does not naturally generate "training-data extraction"; OWASP LLM does not naturally generate "Postgres connection pool exhaustion." The two-framework approach is deliberate: each framework is run independently and the results are unioned.

The CI-enforced linkage to evals is the part that distinguishes this from an aspirational document. T406 fails the build if a new LLM-specific threat is added without a corresponding eval or an explicit "no automated coverage" rationale. That converts the threat model from prose into an artifact whose claims are checkable.

## Consequences

**Positive.**

- The document has a known maintenance contract, not just an authoring date.
- Adversarial coverage is enforced by tests (T406) rather than by reviewer attention.
- New threats automatically pull a TODO into the eval backlog.
- Reviewers who read the threat model can trust it reflects current state — or know exactly where it doesn't (the "no automated coverage" rows).

**Negative.**

- Maintenance overhead: every architecture change touches the document. Mitigated by the PR-template checkbox — the cost is low if the document is touched in the same PR as the change, high if it is deferred.
- The "no automated coverage" rows accumulate technical debt until they're closed. Tracked in Section 4 of THREAT_MODEL.md and surfaced in the team's planning cadence.

**Risk if abandoned.**

- A stale threat model in regulated FS is worse than no threat model. If the cadence cannot be maintained, the document should be marked stale-on-date and the maintenance contract revisited, not allowed to drift.

## Alternatives considered

### PASTA (Process for Attack Simulation and Threat Analysis)

Rejected for being too heavyweight for this scope. PASTA's seven-stage process produces excellent results for large, established systems with a dedicated security team; it is not the right shape for a small project or a small product team to apply consistently. Adopting PASTA would lower the maintenance cadence below quarterly because each pass is too expensive.

### Attack trees

Rejected as the primary structure (kept as a supplementary tool when needed). Attack trees excel at decomposing one threat into many attack paths; they are weaker at producing comprehensive coverage of a system's surface. Useful for deep-dive on individual high-stakes threats (e.g., "passphrase exfiltration" could carry an attack tree as an appendix), not as the core methodology.

### LINDDUN (Privacy-focused threat modelling)

Rejected as the primary methodology because the system's threats are not predominantly privacy-shaped — they are a mix of conversational-AI threats (TM-LLM-001…012), data-handling threats (PII at rest, residency), and operational threats. LINDDUN's seven categories (Linkability, Identifiability, Non-repudiation, Detectability, Disclosure, Unawareness, Non-compliance) are a strong overlay for the privacy-specific subset; we apply LINDDUN concepts in Section 5 of THREAT_MODEL.md (compliance posture) where they fit, without adopting it as the system-wide structure.

### No formal threat model

Rejected. For regulated-financial-services deployment, this is non-negotiable — the absence of a documented threat model is a finding in any third-party assessment. Even for a project at this scale, the document is the artifact that demonstrates a coherent security posture.
