# ZORQ Specialist Orchestration v1

**Status label:** DESIGNED. Future specialist implementations are DEFERRED/NOT VERIFIED.
**Purpose:** coordinate specialized intelligence without letting any specialist self-authorize or bypass the Action Plane.

## 1. Specialist classes

Designed specialist types:

- Research Agent;
- Truth Agent;
- Planning Agent;
- Simulation Agent;
- Optimization Agent;
- Memory Agent;
- Coding Agent;
- Finance Agent;
- Cloud/DevOps Agent;
- Document Agent;
- Device Agent;
- Communications Agent;
- Scheduling Agent;
- future domain-specific specialists.

## 2. Specialist invariant

Every specialist follows:

```text
PROPOSE -> RETURN RESULT -> PROVIDE EVIDENCE -> NEVER SELF-AUTHORIZE
```

## 3. Master Orchestrator responsibilities

The ZORQ Orchestrator:

- interprets goal;
- identifies required specialists;
- retrieves/minimizes context through Memory Firewall;
- creates a work graph;
- parallelizes safe analysis;
- reconciles results;
- detects conflicts;
- requests missing information;
- formulates plan;
- submits only authorized actions for execution;
- collects verification;
- records outcomes.

It must never bypass the Phase 2.6 Action Kernel.

## 4. Orchestration protocol

Each specialist response should include:

- specialist_id and version;
- task_id;
- input context hash/reference;
- result;
- evidence/provenance;
- assumptions;
- uncertainty;
- proposed actions if any;
- required authorization if any;
- safety concerns.

## 5. Conflict handling

Specialist conflicts are routed to Truth/Orchestrator reconciliation. Conflicting proposals cannot be resolved by model confidence alone. If conflict blocks safe action, request information or fail closed.

## 6. Authority boundary

Specialists may reason, code, research, draft, plan, or propose. They may not mutate memory without governance, execute actions without the Action Plane, install capabilities, change grants, or approve their own output.
