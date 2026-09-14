# Memory design

## Why two stores

A vector database answers "what is similar?" but cannot cheaply answer "what
changed, when, and why?". A relational store answers the second question but not
the first. MEMORY//OS writes both on every mutation and treats SQLite as the
system of record.

```sql
memories(id, user_id, content, category, importance, confidence,
         version, source, thread_id, created_at, updated_at)
memory_versions(memory_id, version, content, reason, created_at)
memory_relationships(source_id, target_id, kind, created_at)
memory_events(id, user_id, memory_id, event_type, detail, created_at)
conversations(id, user_id, title, created_at, updated_at)
messages(id, conversation_id, role, content, created_at)
```

SQLite runs in WAL mode behind a thread-local connection wrapper, so FastAPI's
threadpool cannot trip `check_same_thread`.

## Categories

`IDENTITY`, `PREFERENCE`, `COMMUNICATION_STYLE`, `PROJECT`, `GOAL`, `HABIT`,
`RELATIONSHIP`, `FACT`, `CONTEXT`.

Categories are not cosmetic — they feed the category-intent ranking term, so a
query about projects can outrank a more "important" identity memory.

## What gets stored

The policy engine stores a statement only when it is durable and user-specific.

Stored: `"I prefer concise technical explanations."`
Not stored: `"What do you remember about my projects?"`, `"thanks!"`, `"what time is it?"`

Questions are excluded outright. This was a real bug: an early version treated
"What do you remember about my projects?" as a durable fact and stored the
question itself.

## Duplicate vs conflict

Both compare a new statement against existing memories by cosine similarity, and
the distinction is the point of the design:

- **≥ 0.80** — the same fact restated. Reinforce: bump confidence, record
  `MEMORY_REINFORCED`, create nothing new.
- **≥ 0.45**, or **≥ 0.30 with an explicit contradiction signal** — the same
  subject with different content. Supersede: archive the old text into
  `memory_versions`, overwrite the row, increment `version`, re-embed, record
  `MEMORY_SUPERSEDED`.
- Otherwise — a genuinely new memory.

The separate, lower contradiction threshold exists because "Prefers dark
interfaces" and "I have switched to light mode" are semantically *distant* while
being logically contradictory. Lowering the global conflict threshold to catch
that pair would have produced false conflicts everywhere, so the lower threshold
is gated on an explicit contradiction signal instead.

Conflict search deliberately spans categories: a change may be classified
`CONTEXT` while the memory it contradicts is a `PREFERENCE`.

## Versioning

Content is never destroyed by an update. `memory_versions` keeps every prior
revision with the reason it was replaced, and the inspector drawer renders that
history. The current row always holds the newest content.

## Consolidation

Merging several memories writes the merged memory, links each source with a
relationship row, and emits `MEMORY_CONSOLIDATED`. Sources are removed from the
vector index so retrieval cannot return both the merged memory and its parts.

## Isolation guarantees

- **Thread isolation** — conversation state is keyed by `thread_id`; thread A's
  messages never appear in thread B's checkpoint.
- **User namespace isolation** — every query filters on `user_id`; user A's
  memories are unreachable from user B's session, in both SQLite and Chroma.

Both are covered by automated tests.

## Failure behaviour

If embeddings cannot load, the vector store reports `mode: "keyword"` and
retrieval degrades to keyword matching. The API and UI both label this as
KEYWORD FALLBACK rather than continuing to claim semantic search.
