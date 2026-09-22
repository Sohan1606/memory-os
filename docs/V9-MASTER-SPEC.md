# MEMORY//OS V9 — Master Product & Architecture Specification

## 1. Mission

V9 transforms MEMORY//OS from a system that can be operated through conversation into a **conversation-first personal cognitive operating system**.

The user should not think about modules. The user should think:

> I need to talk to my system.

The existing V8 cognitive subsystems remain authoritative internal capabilities. V9 unifies them through a semantic kernel, versioned personal state, and a dynamic cognitive surface.

**North Star: The Surface Follows the Thought.**

## 2. Primary interaction

```
USER SPEAKS / TYPES
        ↓
PERCEPTION
        ↓
MEANING COMPILER
        ↓
COGNITIVE OBJECTS
        ↓
PERSONAL STATE
        ↓
CONTEXT FABRIC
        ↓
COGNITIVE ROUTING
        ↓
MEMORY / WORLD / RESEARCH / PREDICTION / CAUSALITY /
DECISION / ATTENTION / AUTONOMY / LEARNING / EXPLANATION
        ↓
COGNITIVE RESPONSE
        ↓
VISIBLE COGNITIVE SURFACE
        ↓
VOICE + TEXT RESPONSE
        ↓
USER ACTION
        ↓
OBSERVATION / OUTCOME
        ↓
MODEL + MEMORY EVOLUTION
```

Text and voice must enter the same semantic/cognitive pipeline.

## 3. Meaning Kernel

Introduce a canonical semantic layer between natural language and state-changing subsystems.

```
Natural Language
  ↓
Meaning Compiler
  ↓
Semantic Representation
  ↓
Validation
  ↓
Cognitive Object
  ↓
State Transition
  ↓
Cognitive Event Bus
  ↓
Existing Cognitive Systems
```

Natural language must never directly cause arbitrary persistence.

## 4. Cognitive Objects

Initial canonical types:

FACT, OBSERVATION, CLAIM, BELIEF, HYPOTHESIS, PREFERENCE, VALUE,
GOAL, INTENT, NEED, PLAN, COMMITMENT, DECISION, BOUNDARY, QUESTION,
ASSUMPTION, PREDICTION, CONCLUSION, CORRECTION, HYPOTHETICAL,
CONTRADICTION, OUTCOME, EXPERIENCE, SKILL, PRINCIPLE.

Objects should support, where applicable:

- id
- type
- content
- modality
- scope
- temporal scope
- status
- confidence
- provenance/source
- created_at / updated_at
- relationships
- evidence
- supersession

Provenance classes must remain distinct:

USER_STATED, USER_INFERRED, MODEL_HYPOTHESIS, EXTERNAL_EVIDENCE,
SYSTEM_OBSERVED, SYSTEM_DERIVED.

Never silently convert an uncertain statement into a fact, goal, commitment, or belief.

## 5. Meaning Compiler

The compiler converts conversational language into structured meaning while preserving modality and uncertainty.

Example:

> "I think I should leave my current job next year."

must not automatically become an unconditional GOAL.

A valid interpretation can preserve:

- tentative belief/hypothesis
- possible intent
- temporal scope
- uncertainty

The compiler should emit structured semantic candidates and let validation/state policy determine what is recorded.

## 6. Versioned Personal State

Maintain a reconstructable, user-scoped personal state:

USER STATE v1 → v2 → v3 → ...

The state can contain:

values, preferences, goals, intentions, commitments, beliefs,
assumptions, constraints, projects, decisions, open questions,
uncertainties, world dependencies, cognitive policies.

Every material state transition requires provenance and a canonical event.

The system must be able to answer:

- What changed?
- When did it change?
- Why did it change?
- What evidence supported the change?

Never rewrite history to make the current state look inevitable.

## 7. Conversation-first control plane

The main workspace becomes the conversational surface.

The user can:

- type naturally
- speak naturally when browser speech input is available
- receive text responses
- receive speech output where supported
- refer to prior objects using natural references such as "it", "that", "the second option", "continue", "what changed?"
- interrupt or clarify

The user should not need to open cognitive modules during normal operation.

Advanced inspection remains available through Observatory/developer surfaces.

## 8. Dynamic Cognitive Surface

The frontend receives a truthful, structured representation of the current cognitive activity.

Safe visible states may include:

LISTENING
UNDERSTANDING
CHECKING PERSONAL CONTEXT
CHECKING MEMORY
CHECKING WORLD STATE
VERIFYING EXTERNAL INFORMATION
IDENTIFYING UNKNOWNS
EVALUATING CONSEQUENCES
FORMING RESPONSE
WAITING FOR USER
RECORDING OUTCOME
UPDATING MODEL
DEGRADED
NOT CONNECTED
INSUFFICIENT EVIDENCE

Do not expose hidden chain-of-thought.

Do not use fake progress states that imply work which did not happen.

The screen must reflect real backend activity.

## 9. Existing V8 capabilities remain authoritative

Do not rebuild parallel versions of:

- Cognitive Event Bus
- Memory Biology
- Memory Arbitration
- Experience / Skill / Principle
- Intent Evolution
- Need Detection
- World Model
- Context Fabric
- Prediction
- Causality
- Decision Memory
- Counterfactual Sandbox
- Attention
- Autonomy Governor
- Trust / Reputation
- Research
- Explanation
- Self Model
- Portability
- Security / Auth / Audit

V9 integrates with these systems through explicit interfaces.

## 10. EventBus requirement

The existing Cognitive Event Bus remains canonical.

V9 material transitions must produce canonical events, for example:

MEANING_COMPILED
COGNITIVE_OBJECT_CREATED
COGNITIVE_OBJECT_UPDATED
COGNITIVE_OBJECT_SUPERSEDED
PERSONAL_STATE_UPDATED
PERSONAL_STATE_VERSION_CREATED
CONTRADICTION_DETECTED
ASSUMPTION_CREATED
ASSUMPTION_CONFIRMED
ASSUMPTION_REJECTED
INTENT_EVOLVED
SURFACE_SELECTED
VOICE_SESSION_STARTED
VOICE_SESSION_ENDED
COGNITIVE_RESPONSE_GENERATED
OUTCOME_RECORDED

Do not introduce a shadow event history.

## 11. Truth and provenance contract

V9 inherits the V8 honesty contract.

Never claim to have:

- remembered something that was never stored
- verified something that was not verified
- accessed a source that was not accessed
- observed an outcome that was not observed
- executed an action that was not executed
- inferred certainty that the evidence does not support

Preserve explicit states such as UNKNOWN, PROPOSED, UNVERIFIED,
INSUFFICIENT EVIDENCE, NOT CONFIGURED, NOT CONNECTED and DEGRADED.

## 12. User/world/model separation

Maintain separate representations for:

1. What the user said.
2. What the system inferred.
3. What external evidence says.
4. What the system believes with a confidence level.
5. What actually happened.

Never collapse these into one generic memory record.

## 13. Contextual routing

Do not run every capability on every turn.

Reuse the V8.5.1 family-level routing concept and make capability activation contextual.

Ambiguous turns must fail open into a safe response or clarification.

Tool/capability choice must remain genuine where a model is responsible for final selection.

## 14. Cognitive surface contract

Backend returns a structured surface state. Frontend renders it.

Conceptual shape:

```json
{
  "conversation": {},
  "system_state": {},
  "cognitive_stage": "CHECKING_PERSONAL_CONTEXT",
  "active_objects": [],
  "relevant_evidence": [],
  "visible_insights": [],
  "uncertainties": [],
  "next_interaction": {}
}
```

The frontend may choose presentation details, but must not invent cognitive facts.

## 15. Voice

Voice is an interaction mode, not a second intelligence stack:

MIC → speech recognition → same conversation pipeline → response text →
speech synthesis/output.

Reuse existing browser voice input where possible.

Gracefully degrade to text when speech output is unavailable.

## 16. Security

All V8.5 guarantees remain mandatory:

tenant isolation, user isolation, authorization, CSRF protection,
rate limiting, audit logging, correlation IDs and secure session handling.

Semantic references must never bypass authorization.

## 17. Portability

All V9 state must participate in the existing user-owned portability model.

No important V9 state may exist only in transient UI state.

Exports must remain deterministic, versioned, auditable and restorable.

## 18. Failure behaviour

When meaning is materially ambiguous, ask.

When evidence is insufficient, say so.

When several interpretations remain plausible, preserve ambiguity.

When action authority is absent, do not claim completion.

When a connector is unavailable, report its actual state.

## 19. Implementation order

### Phase A — Semantic Core
1. Cognitive Object schema
2. Meaning Kernel
3. Meaning Compiler
4. semantic validation
5. relationships/provenance
6. EventBus integration

### Phase B — Personal State
7. Versioned Personal State
8. state provenance
9. state diff
10. historical reconstruction

### Phase C — Conversation Runtime
11. conversational orchestration
12. semantic context
13. contextual capability routing
14. cognitive activity/status protocol
15. unified voice/text pipeline

### Phase D — Dynamic Surface
16. cognitive surface protocol
17. state-driven renderer
18. conversation-first workspace
19. contextual evidence/insight surfaces
20. safe live activity visualization

### Phase E — Verification
21. deterministic semantic tests
22. persistence/reconstruction tests
23. security/isolation tests
24. conversation continuity tests
25. real-model routing tests
26. voice tests
27. browser tests
28. clean-room tests
29. artifact/release verification

## 20. Non-goals

V9 is not:

- a generic chatbot
- a generic productivity dashboard
- a task manager
- a notification engine
- a generic RAG application
- a pile of disconnected modules
- a fake autonomous agent

Do not add futuristic-looking features unless they strengthen persistent understanding of the person and their changing relationship with reality.

## 21. Acceptance test

A V9 user can say:

> "I'm confused about what I should do next."

without knowing the internal architecture.

MEMORY//OS should determine the relevant context, invoke the appropriate real capabilities, represent the real cognitive activity on screen, answer conversationally, and preserve only the state that was actually established.

The user experiences one system:

MEMORY//OS.

## 22. Future releases enabled by V9

V10: Cognitive Debt + Contradiction Engine + Unknowns + Model Error + Cognitive Health
V11: Personal Laws
V12: Identity Evolution
V13+: Personal Worldline

Long-term north-star question:

> What changed, why did it change, what caused it, what did we learn, and what should change because of it?
