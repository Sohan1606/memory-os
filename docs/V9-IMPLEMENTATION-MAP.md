# V9 implementation map

This map was produced from the existing repository before implementation and
records the reuse decisions.

| V9 concern | Existing authority reused | V9 extension |
|---|---|---|
| Composition | `Runtime`, `Cognition` | `MeaningKernel`, `PersonalStateService`, `CognitiveSurface` wired into the same roots |
| Persistence | `Database`, additive schema constants/migrations | five additive semantic tables |
| Audit/events | `cognition.events.EventBus`, `cognitive_events` | semantic/state/surface event vocabulary on the same bus |
| Context | `ContextBuilder`, bounded sections and honesty state | relevance-ranked `semantic` section |
| Routing | `CapabilityRouter`, V8.5.1 tool families | unchanged genuine model selection; semantic context is available before selection |
| Object permanence | `FocusTracker` | `cognitive_object` focus kind |
| Memory | `MemoryService`, policy, vector/keyword retrieval | not replaced; semantic state does not masquerade as memory retrieval |
| World/mission/intent | existing V8 services | not duplicated as operational state |
| Learning | `ExperienceStore`, `KnowledgeService` | semantic EXPERIENCE/SKILL/PRINCIPLE remain user meaning until V8 validation promotes operational knowledge |
| Explanation | `ExplanationEngine` | semantic provenance/state inspectable; no chain-of-thought |
| Voice | `useSpeech`, `/api/chat` | interaction-mode events and optional browser speech output on the same pipeline |
| Security | middleware, `uid()`, principal namespace | every V9 query filters by verified namespace |
| Portability | `PortabilityService` | `semantic_state` domain and V9 tables |
| Primary UI | existing workspace/Chat | conversation-first layout + backend-driven surface |
| Inspection | existing Observatory | semantic state panel |
