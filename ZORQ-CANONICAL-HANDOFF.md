# Sohan's ZORQ — Canonical Repository Handoff

## Purpose

This repository is being evolved from the MEMORY//OS project into Sohan's ZORQ.

MEMORY//OS is not being discarded. It remains the canonical memory and cognitive-governance subsystem inside ZORQ.

The repository itself is becoming the canonical home for ZORQ.

## GitHub baseline

Current main baseline before migration:
- release: MEMORY//OS v10.2.0
- commit: 2c73c37ddcd1dd88265759c340e878b5573c1479

Migration branch:
- zroq/canonical-migration

## Latest cumulative ZORQ implementation

The latest verified cumulative package is:

ZORQ-PHASE3C.1-CONCURRENT-INTERRUPTION-VERIFIED.zip

SHA-256:
bf6477b8320871ef93208c62d2dc34f039a8a72f093fe860d281150605e60f64

It contains the cumulative ZORQ implementation through:
- Phase 2.6 execution integrity
- Phase 3A schemas/contracts
- Phase 3A.1 contextual memory activation
- Phase 3B personal continuity
- Phase 3B.1 temporal recall/timezone correctness
- Phase 3C conversational runtime
- Phase 3C.1 concurrent interruption/control correctness

Reported Phase 3C.1 verification:
- 256 total tests
- 248 executed
- 8 skipped
- 0 failed
- 0 errors
- clean-extraction regression PASS

## Important verification limits

The cumulative Phase 3C.1 package was verified in its build environment, but:
- Windows execution has not yet been verified on the owner's machine.
- Production MEMORY//OS integration has not yet been verified.
- Voice/STT/TTS, wake word, browser/GUI automation, proactive daemon behavior, broad OS control, and other later capabilities are intentionally not yet implemented.

## Canonical architectural rules

1. MEMORY//OS remains the canonical memory/governance authority.
2. ZORQ must not create a competing memory authority.
3. ZORQ must not create a competing cognitive policy authority.
4. Action Kernel remains the execution authority.
5. Model confidence is not authorization.
6. Tool availability is not permission.
7. Wake word is not authentication.
8. Memory relevance is not action authority.
9. Provider acceptance is not verified outcome.
10. Completed execution is not verified outcome.
11. External data is not trusted instruction.
12. Historical truth must remain distinguishable from current truth.
13. No fake capabilities or simulated success presented as real.
14. New capabilities must remain extensible without rewriting the core authority architecture.

## Required local migration sequence

1. Start from the current main repository.
2. Check out zroq/canonical-migration.
3. Import the cumulative Phase 3C.1 package into the repository without deleting the existing MEMORY//OS implementation.
4. Run the ZORQ package tests.
5. Run the existing MEMORY//OS regression suite.
6. Run frontend/backend build and type checks where applicable.
7. Verify the merged tree on Windows.
8. Only after the local verification passes should the migration branch be promoted to main.
9. Use the resulting GitHub repository as the canonical source for future Arena sessions.

## Next engineering milestone

Master ZORQ Requirements Reconciliation → final dependency graph → Phase 3D Voice Runtime.

Do not begin later capability work until the canonical repository contains the verified cumulative baseline.