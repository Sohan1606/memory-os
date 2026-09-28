# ZORQ Memory Retrieval Implementation v1

**Status:** IMPLEMENTED / VERIFIED locally.

## Retrieval modes

Implemented modes:

- `EXACT` — source IDs, message IDs, conversation IDs, exact temporal/date filters;
- `LEXICAL` — SQLite FTS5 full-text search with fallback;
- `SEMANTIC` — local deterministic concept-vector provider (`local-concept-vector-v1`), not a hosted or neural embedding provider;
- `TEMPORAL` — exact date, year/date ranges, timestamp ranges;
- `RELATIONAL` — project/entity/goal/decision overlap;
- `CAUSAL` — decision/event/outcome derived-memory evidence for “why/what led to/what changed” queries.

## Semantic availability

Semantic retrieval status is explicit:

```text
AVAILABLE:local-concept-vector-v1
```

This is truthful local concept-vector retrieval, not a vector database and not lexical FTS relabeled as semantic. No external provider or network call is required.

## Temporal normalization

Canonical storage timestamps: UTC. Source records preserve event time, ingest time, local display time, timezone name, and UTC offset where supplied.

Calendar-date interpretation: owner/query timezone. Date-only user queries such as “27 September 2026” are interpreted as calendar days in the explicit query timezone when supplied, otherwise the owner calendar timezone from `MemoryAccessContext`, otherwise the documented UTC fallback. The local calendar-day bounds are converted to UTC before querying stored source records.

The implementation uses Python `zoneinfo` for timezone and DST boundaries; timezone arithmetic is not hand-rolled. Exact timestamp queries with an explicit `Z` or offset remain absolute UTC/offset timestamp queries.

## Exact historical recall

The answer path for “What did we talk about on 27 September 2026?” retrieves source messages first and reports exact evidence. Summary/inference text is secondary and explicitly labeled.

## Efficiency

The retrieval pipeline is staged:

```text
current/query context
→ indexed temporal/relational/lexical candidates
→ bounded semantic scoring
→ governed expansion
→ source evidence
→ minimized context
```

The implementation does not claim unlimited scale and does not load the entire archive for normal retrieval.
