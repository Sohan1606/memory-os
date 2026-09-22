# Cognitive architecture (v8 → v9)

V9 adds a validated Meaning Kernel, versioned Personal State and a safe
backend-driven Cognitive Surface while reusing the V8 systems below. See
[V9-ARCHITECTURE.md](V9-ARCHITECTURE.md).

The v8 layer sits on top of the v7 memory system. It does not replace it: the
agent, memory service, vector store and providers are unchanged. What v8 adds is
a layer that *understands, records and explains* — and that refuses to state
anything it has not measured.

## Honesty contract

This is the rule the whole layer is built around:

> Never present an unmeasured value as a measured one.

Concretely, every subsystem returns one of these instead of guessing:

| State | Meaning |
|---|---|
| `INSUFFICIENT EVIDENCE` | Not enough recorded observations to answer. |
| `NOT CONFIGURED` | The capability exists but is not set up in this deployment. |
| `NOT CONNECTED` | An external source that has no connector configured. |
| `DEGRADED` | Working, but through a fallback path. |

There are no random numbers anywhere in this layer. Reputation, trust, accuracy
and calibration are all counts of recorded events.

## The event bus

`app/cognition/events.py` is the single source of truth. Everything the system
does emits an event into the append-only `cognitive_events` table.

- **Closed vocabulary.** `EVENT_TYPES` holds 69 types and `emit()` raises
  `ValueError` on anything else. This prevents silent vocabulary drift — it
  caught a real bug during development (`conversation.reply` vs
  `conversation.response`).
- **Human labels.** Every type maps to plain language in `LABELS`
  (`memory.created` → "Remembered something new"). The primary UI shows the
  label; Developer mode shows the raw type.
- **Correlation.** Every event in one conversational turn shares a
  `correlation_id`, so a turn can be replayed exactly.
- **Subjects.** `subject_kind` / `subject_id` give every object its own history,
  which is what powers "Why do you think that?".
- **Subscriber isolation.** Subscribers run in try/except: observability can
  never break a conversation.

## Subsystems

| Module | Responsibility |
|---|---|
| `events.py` | Canonical event bus |
| `world.py` | Living world model (goals, projects, commitments, people, risks) |
| `intent.py` | Probabilistic intent + need detection, intent evolution |
| `prediction.py` | Predictions, Brier calibration, surprise detection |
| `causality.py` | Causal graph + decision log with computed regret |
| `reputation.py` | Memory lifecycle, reputation, arbitration |
| `autonomy.py` | Autonomy governor, trust calibration, attention engine |
| `learning.py` | Policies, skills, principles, self-evaluation |
| `sandbox.py` | Counterfactual simulation (never mutates state) |
| `self_model.py` | Capabilities, limitations, continuity, recovery |
| `orchestrator.py` | Composition root; runs the turn |

## The cognitive turn

`Cognition.process_turn()` runs alongside the agent — it builds understanding
but does not generate the reply. If it fails, the conversation still succeeds and
the failure is recorded.

```
perceive → need/intent → world → retrieve → arbitrate
        → predict → attention → respond → learn
```

## Three separate dimensions

These are deliberately never conflated:

- **Confidence** — how likely a memory is to be *true*.
- **Reputation** — how well *acting on it* has worked out. Earned only from
  recorded outcomes; starts at `INSUFFICIENT EVIDENCE`.
- **Authority** — whether the system may act *without asking*. Governed by the
  autonomy level, risk class and reversibility.

A high-confidence memory informs a recommendation. It never authorizes an
irreversible action: at *every* autonomy level, irreversible or high-impact work
returns `ask`. Demonstrated action failures withdraw automatic authority.

## Memory biology

Lifecycle advances on evidence, never on a timer:

```
candidate → validating → trusted → reinforced
                      ↘ uncertain → contradicted → retired
```

A memory is never promoted to `trusted` on a single observation.

## Arbitration

When memories compete, `MemoryArbiter` scores each on confidence (0.30),
recency (0.25), source authority (0.20), reputation (0.15) and specificity
(0.10), then reports the winner *and* why the others lost. A recent explicit
correction beats a stale general preference. Margins under 0.12 are flagged as a
genuine conflict rather than resolved silently.

## Attention

The attention engine must be able to conclude **do nothing**. Expected value is
`importance × urgency × confidence`, minus an interruption cost. Suppressed
interventions are logged as `intervention.suppressed` — choosing not to
interrupt is a real decision worth inspecting.

## Sandbox

Simulations read a snapshot of world state and write only to `sandbox_runs`.
Tests assert that world state is byte-identical before and after a simulation,
and the UI labels every result `SIMULATION ONLY — NO REAL CHANGES`.

## What is NOT implemented

Stated plainly so the docs do not overclaim:

- **No external connectors.** Calendar, email and files are `NOT CONNECTED`.
  There is no integration code for them.
- **No actions outside the memory store.** The system cannot send email, modify
  files or call third-party APIs.
- **No ROI measurement.** `time_saved` and `cost_avoided` always return
  `INSUFFICIENT EVIDENCE`; they are not measurable in this deployment.
- **Extraction is deterministic**, not model-driven, unless a tool-calling model
  is configured. Intent, need, world entities and predictions come from explicit
  rules over the user's own words — not from an LLM.

---

## V8.2 — the Cognitive Core

V8.1 could state what it believed. V8.2 records **why**, **what that belief
changed**, and **whether it was right** — and refuses to answer where it has not
observed.

### The honesty rules, stated as invariants

These are the rules the V8.2 test suite exists to defend. Each is enforced in
code, not just documented.

1. **Retrieval is not influence.** Retrieving a memory records a usage count.
   It never moves reputation. Only a memory that won arbitration and entered the
   decision path is recorded as an influence.
2. **No evidence, no reputation change.** An outcome claiming `SUPPORTED` with
   an empty evidence list is downgraded to `INSUFFICIENT EVIDENCE`.
3. **Confidence ≠ reputation.** They are separate fields, computed from separate
   inputs, and a high-confidence memory with no track record reads
   `INSUFFICIENT EVIDENCE`.
4. **A question never asserts intent.** *"Should I migrate to Postgres?"*
   creates nothing.
5. **An unconfirmed goal never displaces a confirmed one.** A second objective
   without an explicit change signal is `emerging`, surfaced separately.
6. **A prediction is scored only when reality resolved it.** Mentioning the
   topic is not evidence.
7. **A bad outcome is not proof of a bad decision.** Regret with no recorded
   expectation and no cited evidence is `INSUFFICIENT EVIDENCE`, not a number.
8. **UNKNOWN never satisfies a requirement.** An unrecognised model is not
   assumed to support tool calling.
9. **Never guess a destructive target.** "Forget that" with nothing focused, or
   with two equally good matches, asks instead of deleting.
10. **Truncation and degradation are declared**, never silent.
11. **No hidden chain-of-thought is exposed.** Explanations cite sources,
    confidences, reputations and recorded transitions.

### The causal chain

```
memory ──retrieved──▶ (usage count only, no judgement)
   │
   └──won arbitration──▶ influence ──observed outcome + evidence──▶ reputation
                             │                                          │
                             └── no outcome yet ──▶ INSUFFICIENT EVIDENCE
```

Each arrow is a persisted row. `GET /api/memories/{id}/impact` walks the whole
chain and reports only as far as the evidence allows.

### Arbitration factors

| Factor | Weight |
|---|---|
| confidence | 0.20 |
| authority (correction > explicit > conversation > inference) | 0.18 |
| recency | 0.15 |
| scope match | 0.12 |
| reputation | 0.12 |
| specificity | 0.08 |
| freshness | 0.08 |
| explicit correction | 0.07 |
| contradiction penalty | −0.15 each, capped at −0.4 |

`STALE_AFTER_DAYS = 120`. A `superseded` memory is blocked outright. Lifecycle
`retired` / `quarantined` memories are excluded from retrieval and listed in
`excluded` — visible, not silently dropped.

### Execution tracing

Eleven stages, persisted per turn and addressable by `correlation_id`.
Four bounds: depth cap (4), timeout (180 s), duplicate-call detection, and
cooperative cancellation. A tool failure returns `TOOL_ERROR: …` to the model so
it can recover; it does not abort the turn.

### The five autonomy dispositions

| Disposition | When |
|---|---|
| `ACT` | Low risk, reversible, within your autonomy level |
| `ASK` | Capable, but risk exceeds unilateral authority |
| `WAIT` | Not enough evidence to act *or* ask a useful question |
| `DO_NOTHING` | Observe-only: staying out of the way is the right answer |
| `BLOCKED` | Irreversible / external, or a demonstrably poor track record |

`decision` remains `act`/`ask` for V8/V8.1 compatibility.

### What is NOT implemented in V8.2

- **No external connectors.** Calendar, email and files are declared
  `NOT CONNECTED` in every context bundle and contribute zero items.
  `UNCONNECTED` is a valid end state, not a placeholder for fake data.
- **No cross-user learning.** Everything is per-user-namespace.
- **No independent investigation.** Regret and outcomes are scored from evidence
  the caller supplies; the system does not go looking for it.
- **No vision** without a vision model — reported `NOT_CONFIGURED`, never
  downgraded to a guess.
- **Semantic continuity decay** is time-based (45 days), not meaning-based.

---

## V8.3 — Continuous Cognition

V8.3 closes the loop between turns. See [`V8.3.md`](V8.3.md) for the full
design; this section records how the new pieces relate to the V8.2 systems they
extend.

| V8.3 system | Extends | Relationship |
|---|---|---|
| `observation.ObservationLog` | — | New canonical evidence layer that the other systems record into |
| `missions.MissionRegistry` | the unused `mission.*` event vocabulary | Implements the entity those events were reserved for |
| `world_v2.WorldStateV2` | `world.WorldModel` | Wraps it; adds change provenance, staleness, reconciliation. Does not replace it |
| `attention.AttentionEngineV2` | `autonomy.AttentionEngine` | Wraps its scoring; adds relevance, mission context, learned silence |
| `simulation.SimulationEngine` | `sandbox.Sandbox` | Reuses its projection; adds snapshot isolation, `SIMULATED` tagging, commit guards |
| `maintenance.MaintenanceV2` | `health.MemoryHealthEngine` | Adds a read-only review safe for background use, and evidence-gated reinforcement |
| `background.BackgroundCognition` | — | Orchestrates the above under strict bounds |
| `timemachine.TimeMachine` | `world_changes`, `mission_events` | Reads history the V8.3 writers now produce |

### Epistemic discipline (§39)

The five states — `OBSERVED`, `INFERRED`, `PREDICTED`, `SIMULATED`, `UNKNOWN` —
are stored, not inferred at display time, and are never silently upgraded. The
promotion rule enforces the boundary that matters most: **only what was
genuinely observed may become a durable memory.**

A simulation is not a fact. A prediction is not an observation. A stale fact is
not a false one. An empty background cycle is not activity. Each of these
distinctions is asserted by a test.

---

## V8.3.1 — how the conversation reaches cognition

V8.3 gave the system missions, world history, predictions, attention and
simulation. V8.3.1 connects them to the only interface that matters to a user:
talking.

### The path

```
POST /api/chat
  ├─ cognition.process_turn(...)    understanding (intent, world, prediction)
  └─ agent.run(...)
       load_context   ContextBuilder → MISSION section first, then memory,
                      episodic, goals, commitments, world, intent,
                      preferences, decisions, predictions, capability
       agent          model sees 5 memory tools + 15 cognitive tools
       tools          one traced executor for both families
       memory_manager response
```

`MemoryAgent._tools_for` is the single place the toolset is built, so the
deterministic planner and the model path can never drift apart, and cognitive
tools inherit the existing MODEL_CALL / TOOL_DECISION / TOOL_RESULT /
MODEL_REVISION / FINAL_RESPONSE tracing without new tracing code.

### Why the tools return sentinels

A tool that returns `{}` invites a language model to fill the silence. Every
cognitive tool therefore returns an explicit statement of absence —
`NO_MISSIONS_RECORDED`, `NO_NEXT_STEP_RECORDED`, `NO_BLOCKERS_RECORDED`,
`NO_CHANGES_RECORDED`, `HISTORY_NOT_AVAILABLE`, `NOTHING_RECORDED`,
`INSUFFICIENT EVIDENCE` — and the system prompt instructs the model to repeat
those plainly.

This is the same epistemic discipline as §39, pushed one layer outward: it is
no longer enough for the *store* to be honest, the *sentence* must be too.

### Focus as conversational memory

`FocusTracker` was V8.2 machinery for "what is open in the inspector". In
V8.3.1 the conversation writes to it as well, keyed by `thread_id`, so a
pronoun has a referent. Resolution order for a mutating tool with no explicit
id:

1. the object currently in focus for this thread;
2. if nothing is focused and exactly one mission is open, that one;
3. otherwise `AMBIGUOUS_REFERENCE` with the candidate list — the assistant
   asks, and never picks.

The tracker's existing refusal logic was kept intact; only the set of focusable
kinds and the reference vocabulary grew.

### Attention vs. a direct question (§14)

Background attention decides what is worth *raising unprompted*. It has no
authority over what may be *answered when asked*. `get_attention_state` reports
real background findings and real suppressions; a suppressed topic is still
disclosed when the user asks directly, because suppression is about
interruption, not secrecy.
