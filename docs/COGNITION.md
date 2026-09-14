# Cognitive architecture (v8)

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
