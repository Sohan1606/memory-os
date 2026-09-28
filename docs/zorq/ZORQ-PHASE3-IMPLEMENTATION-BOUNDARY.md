# ZORQ Phase 3 Implementation Boundary

**Status label:** DESIGNED. No Phase 3 implementation is performed in this architecture revision.
**Purpose:** divide future work into controlled phases and identify the smallest first implementation slice.

## Phase boundaries

| Phase | Scope | Explicitly not included |
|---|---|---|
| PHASE 3A | Master architecture + foundational schemas | runtime memory/voice/browser/device expansion |
| PHASE 3B | Real MEMORY//OS adapter + Personal Continuity Engine | broad action expansion, voice, browser |
| PHASE 3C | Conversation Runtime + text interaction | voice/barge-in, browser, daemon |
| PHASE 3D | Truth Engine + research | automated external actions |
| PHASE 3E | Simulation + optimization | silent behavior changes |
| PHASE 3F | Voice + barge-in + multilingual interaction | voice-only high-assurance authorization |
| PHASE 3G | Specialist orchestration | specialist direct execution |
| PHASE 3H | Controlled real-world capability expansion | universal shell/browser/application tool |
| PHASE 3I | Controlled evolution | silent security/policy self-modification |

Do not implement later phases during earlier phases.

## First implementation slice

The first implementation should prove central continuity, not broad automation.

Preferred slice:

1. persistent conversation storage;
2. MEMORY//OS-governed storage/retrieval boundary;
3. temporal retrieval;
4. exact historical conversation recall;
5. structured memory extraction;
6. provenance;
7. deletion semantics;
8. conversation-state continuity.

Demo requirement:

```text
Conversation on 27-09-2026
-> stored
-> indexed
-> governed
-> later queried
-> exact record retrieved
-> answer grounded in original evidence
```

## Out of scope for first slice

- browser automation;
- broad Windows control;
- voice;
- arbitrary PowerShell;
- autonomous background actions;
- generic shell;
- generic application launch;
- production MEMORY//OS claims before verified adapter;
- capability expansion beyond Phase 2.6 unless explicitly reviewed.

## Acceptance criteria for first slice

- raw source conversation retained separately from derived memory;
- exact source retrieval by date/conversation/message ID;
- structured memory item with provenance;
- temporal query support for at least specific date and range;
- deletion request creates propagation plan and truthful status;
- conversation checkpoint restored after restart;
- Memory Firewall demonstrates minimization before model/provider context;
- all governed operations fail closed when MEMORY//OS governance is unavailable;
- no change to Phase 2.6 Action Kernel authority.

## Stop condition

This architecture revision stops before Phase 3A/3B implementation. The next phase must be selected only after independent review of these architecture documents.
