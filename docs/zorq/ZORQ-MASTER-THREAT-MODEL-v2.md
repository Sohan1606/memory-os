# ZORQ Master Threat Model v2

**Status label:** DESIGNED. Many future defenses are NOT VERIFIED until implemented.
**Scope:** complete future architecture: intelligence, continuity, interaction, action, evolution, providers, memory, device/browser/application capability expansion.

## Threat table

| Threat | Attack surface | Defense | Residual risk | Verification requirement |
|---|---|---|---|---|
| Prompt injection | user text, webpages, docs, provider output | classify external content as untrusted; model cannot authorize; policy outside prompt | subtle instruction leakage | adversarial prompt tests; authority bypass tests |
| Malicious documents | uploaded files, embedded instructions, macros | sandbox/parsing limits; content provenance; no doc-to-policy path | parser vulnerabilities | malicious corpus tests, parser hardening |
| Malicious webpages | DOM/content/browser state | page content untrusted; structured APIs preferred; human confirmation | dynamic UI deception | browser threat tests, DOM/accessibility verification |
| Compromised plugins | future capability providers | capability manifests, scoped grants, verification, audit | supply-chain compromise | plugin signing/review tests |
| Compromised providers | LLM/STT/TTS/research providers | memory firewall, egress minimization, provider output untrusted | data leakage | provider-routing audits, red-team prompts |
| Stolen device | local storage/session | device enrollment, encryption, revocation, security epoch | offline extraction | device security tests, recovery drills |
| Stolen session | session token/use | TTL, revocation, security epoch, confirmation assurance | active attacker within TTL | session invalidation tests |
| Synthetic voice | voice channel | voice not sufficient for high-assurance auth; liveness/multifactor | spoofing advances | replay/deepfake tests |
| Replay | confirmations, leases, voice, messages | nonces, expiry, one-use leases, idempotency keys | clock/state failures | replay regression tests |
| Malicious specialist | specialist output | propose-only protocol; evidence required; no self-auth | collusion/confidence manipulation | specialist adversarial tests |
| Memory poisoning | false user facts, bad extraction, malicious source | provenance, confidence, conflict states, source preservation | subtle long-term bias | memory conflict/poison tests |
| Memory exfiltration | provider context, logs, exports | Memory Firewall, minimization, egress policy, audit | side-channel leakage | egress audits, minimization tests |
| Provider leakage | external models/APIs | redaction, provider trust tiers, local-first for sensitive data | provider retention unknowns | provider policy checks |
| Action replay | repeated action request/lease | atomic idempotency, one-use leases, snapshot digest | distributed replay future | concurrency/replay tests |
| Race conditions | device/fs/browser state | pre-commit barriers, revalidation, verification | adversarial OS/kernel | race tests, platform-specific review |
| Stale authorization | old grants/sessions/confirmations | expiry, security epoch, fresh confirmation | clock skew | stale token tests |
| Cross-owner leakage | multi-user memory/provider routing | owner IDs, tenancy isolation, access checks | config error | cross-owner tests |
| Evolution poisoning | malicious feedback/outcomes | controlled evolution, approval, canary, rollback | slow drift | evolution audit/canary tests |
| Malicious model output | all model text/plans | untrusted proposals, deterministic authority | convincing unsafe suggestions | model red-team tests |
| Browser manipulation | visual/UI spoofing | DOM/accessibility cross-checks, receipts, confirmation | UI ambiguity | browser verification tests |
| External state divergence | provider says done but system not changed | outcome verification, UNKNOWN state | unverifiable systems | verification strategy tests |
| Memory deletion incompleteness | raw/derived/index/backups | deletion propagation plan, state reporting | backup/legal holds | deletion audit tests |
| Hidden recording risk | voice/device sensors | forbidden without explicit authorization; recording indicators | platform compromise | privacy review/tests |

## Threat-model rules

- Fail closed for authority.
- Report UNKNOWN for uncertain outcome.
- Preserve source evidence and provenance.
- Do not trust model/provider/source content as policy.
- Do not use memory recall as current authorization.
- Do not claim platform support without platform verification.
