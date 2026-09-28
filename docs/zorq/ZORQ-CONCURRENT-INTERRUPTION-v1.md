# ZORQ Phase 3C.1 Concurrent Interruption v1

Status: implemented and verified in the local standalone ZORQ project.

Phase 3C.1 closes a reliability race in Phase 3C where a streaming provider could continue after a
user STOP and later overwrite the durable `INTERRUPTED` state with `COMPLETED`.

## Reproduced pre-fix race

Before source changes, a deterministic blocking provider was used:

1. response started;
2. `TEXT_DELTA("Alpha ")` was emitted and persisted;
3. provider blocked;
4. a second thread sent STOP;
5. runtime persisted `INTERRUPTED` with prefix `"Alpha "`;
6. provider was released and intentionally ignored cancellation;
7. provider emitted `TEXT_DELTA("Beta ")` and `RESPONSE_COMPLETED`.

Observed pre-fix result:

```text
STATE_IMMEDIATELY_AFTER_STOP= INTERRUPTED
TEXT_IMMEDIATELY_AFTER_STOP= 'Alpha '
STOP_RESULT_STATE= INTERRUPTED
STATE_AFTER_PROVIDER_RESUMES= COMPLETED
TEXT_AFTER_PROVIDER_RESUMES= 'Alpha Beta '
THREAD_RESULT_STATE= COMPLETED
THREAD_RESULT_TEXT= 'Alpha Beta '
PROVIDER_EVENTS= ['RESPONSE_STARTED', 'TEXT_DELTA:Alpha ', 'TEXT_DELTA:Beta ', 'RESPONSE_COMPLETED']
SOURCE_RECORDS= [('user', 'please stream concurrently'), ('assistant', 'Alpha '), ('assistant', 'Alpha Beta ')]
```

This was the bug: STOP marked the response interrupted, but the stale producer later finalized it as
completed and created a contradictory assistant source record.

## Fixed post-3C.1 behavior

The same reproduction after the fix:

```text
STATE_IMMEDIATELY_AFTER_STOP= INTERRUPTED
TEXT_IMMEDIATELY_AFTER_STOP= 'Alpha '
STOP_RESULT_STATE= INTERRUPTED
STATE_AFTER_PROVIDER_RESUMES= INTERRUPTED
TEXT_AFTER_PROVIDER_RESUMES= 'Alpha '
THREAD_RESULT_STATE= INTERRUPTED
THREAD_RESULT_TEXT= 'Alpha '
PROVIDER_EVENTS= ['RESPONSE_STARTED', 'TEXT_DELTA:Alpha ', 'TEXT_DELTA:Beta ']
SOURCE_RECORDS= [('user', 'please stream concurrently'), ('assistant', 'Alpha ')]
```

The late `Beta` event was consumed from the provider but rejected as stale before it could be
committed.

## Key invariant

A stale streaming producer must never overwrite a newer durable control decision.

The runtime enforces this before:

- accepting `TEXT_DELTA`;
- persisting a delta;
- accepting memory/evidence references;
- finalizing `COMPLETED`;
- finalizing `FAILED`/`CANCELED`;
- creating completed source records.

## What Phase 3C.1 provides

Phase 3C.1 provides deterministic cooperative interruption control for text streaming:

- out-of-band control methods independent of ordinary user-message ingestion;
- persistent response control state;
- monotonic response control epoch;
- stale stream generation rejection;
- deterministic terminal precedence;
- concurrency tests using real Python threads and synchronization events;
- restart-aware persisted response control state.

## What Phase 3C.1 does not provide

Phase 3C.1 does not implement:

- microphone barge-in;
- STT;
- TTS;
- speech output cancellation;
- wake word;
- speaker verification;
- voice cloning;
- OS process termination;
- browser or GUI automation;
- daemon/background autonomy;
- production real-time voice guarantees;
- production MEMORY//OS integration.

Voice and real barge-in remain future Phase 3D scope.
