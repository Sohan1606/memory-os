# ZORQ Memory Firewall Implementation v1

**Status:** IMPLEMENTED / VERIFIED locally.

## Purpose

The Memory Firewall sits between personal memory and reasoning/provider context.

Implemented controls:

- owner isolation;
- task purpose;
- task scope;
- privacy class;
- sensitivity;
- provider trust class;
- egress policy;
- minimization;
- redaction;
- blocked-memory exclusion.

## Provider egress

`LOCAL_ONLY` + `NO_EGRESS` is implemented and tested. External/untrusted provider context with no egress is denied.

No personal memory is sent to external providers in this phase.

## Minimization

The firewall does not inject the full archive. It admits only selected source IDs, temporal state, relevance state, policy-safe summaries, and provenance references needed for reasoning.

## Redaction and block semantics

Phase 3B also fixes the Phase 3A.1 hygiene issue:

- `redacted_candidate_ids` require an actual REDACT decision;
- blocked candidates cannot be treated as redacted usable context;
- blocked memory markers cannot leak through reasoning context;
- blocked candidates remain blocked.
