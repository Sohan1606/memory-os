"""V10.2 evidence-governed policy evolution foundation.

This module deliberately sits above CognitivePolicyEngine.  It never computes a
competing effective policy and never calls a model.
"""
from __future__ import annotations
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DOMAINS = {"ATTENTION", "DECISION", "AUTONOMY", "MODEL_HANDLING"}
SIGNALS = {"beneficial", "neutral", "harmful", "insufficient_evidence"}
STATES = {"CANDIDATE", "VALIDATING", "PROPOSED", "ACCEPTED", "ACTIVE", "WEAKENED", "DEFERRED", "SUPERSEDED", "EXPIRED", "REJECTED"}
TRANSITIONS = {
    "CANDIDATE": {"VALIDATING", "REJECTED"}, "VALIDATING": {"PROPOSED", "REJECTED"},
    "PROPOSED": {"ACCEPTED", "REJECTED"}, "ACCEPTED": {"ACTIVE", "REJECTED"},
    "ACTIVE": {"WEAKENED", "DEFERRED", "SUPERSEDED", "EXPIRED"},
    "WEAKENED": {"DEFERRED", "SUPERSEDED", "EXPIRED"},
    "DEFERRED": {"PROPOSED", "SUPERSEDED", "EXPIRED"},
}

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

@dataclass(frozen=True)
class Evidence:
    id: str
    provenance: str
    kind: str
    payload: dict[str, Any]
    correlation_id: str | None = None

class PolicyEvidenceAssembler:
    def __init__(self, db, bus): self.db, self.bus = db, bus
    def assemble(self, user_id: str, candidate: dict[str, Any], *, correlation_id: str | None = None) -> list[Evidence]:
        """Read canonical records only; returned evidence is deterministic and scoped."""
        out: list[Evidence] = []
        key = candidate.get("target") or candidate.get("key")
        if key:
            row = self.db.query_one("SELECT * FROM policies WHERE user_id=? AND key=?", (user_id, key))
            if row:
                out.append(Evidence(f"policy:{user_id}:{key}", "CognitivePolicyEngine", "adaptation", {"key": key, "value": row["value"], "confidence": row["confidence"] if "confidence" in row.keys() else 0.0}))
        if correlation_id:
            for event in self.bus.for_correlation(correlation_id):
                if event.user_id != user_id: continue
                kind = "outcome" if event.type.startswith(("outcome.", "prediction.")) else "adaptation" if event.type.startswith("policy.") else None
                if kind:
                    out.append(Evidence(f"event:{event.id}", "EventBus", kind, {"type": event.type, "summary": event.summary, "payload": event.payload}, correlation_id))
        return out

class LearningSignalDeriver:
    def derive(self, evidence: list[Evidence] | list[dict[str, Any]]) -> dict[str, Any]:
        outcomes = [e for e in evidence if (e.kind if isinstance(e, Evidence) else e.get("kind")) == "outcome"]
        if not outcomes: return {"signal": "insufficient_evidence", "evidence_ids": [e.id if isinstance(e, Evidence) else e.get("id") for e in evidence], "reason": "No observed outcome evidence; adaptation alone is not success."}
        text = json.dumps([e.payload if isinstance(e, Evidence) else e.get("payload", {}) for e in outcomes]).lower()
        signal = "harmful" if any(x in text for x in ("harmful", "failed", "failure", "negative")) else "beneficial" if any(x in text for x in ("beneficial", "success", "successful", "positive")) else "neutral"
        return {"signal": signal, "evidence_ids": [e.id if isinstance(e, Evidence) else e.get("id") for e in outcomes], "reason": "Derived only from observed outcome evidence."}

class PolicyGovernanceService:
    def __init__(self, db, bus, policy_engine=None): self.db, self.bus, self.policy_engine = db, bus, policy_engine
    def create(self, user_id: str, *, domain: str, target: str, proposed_value: str, reason: str, evidence_refs: list[str], correlation_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        if domain not in DOMAINS or not evidence_refs or not correlation_id: raise ValueError("valid domain, evidence_refs, and correlation_id are required")
        cid = f"adapt_{uuid.uuid4().hex[:16]}"; stamp = now()
        self.db.execute("INSERT INTO policy_governance (id,user_id,tenant_id,domain,target,proposed_value,state,reason,evidence_refs,correlation_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (cid,user_id,tenant_id,domain,target,proposed_value,"CANDIDATE",reason[:500],json.dumps(evidence_refs),correlation_id,stamp,stamp))
        return self.transition(user_id, cid, "VALIDATING", reason="candidate validation", evidence_refs=evidence_refs, correlation_id=correlation_id)
    def transition(self, user_id: str, candidate_id: str, new_state: str, *, reason: str, evidence_refs: list[str], correlation_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM policy_governance WHERE id=? AND user_id=?", (candidate_id,user_id))
        if not row or new_state not in STATES or new_state not in TRANSITIONS.get(row["state"], set()): raise ValueError("invalid governance transition")
        if not reason or not evidence_refs or not correlation_id: raise ValueError("reason, evidence_refs, and correlation_id are required")
        if new_state == "ACTIVE" and self.policy_engine is not None:
            from .policy_engine import STRONG, DIMENSIONS
            if row["domain"] != "MODEL_HANDLING" and row["target"] not in DIMENSIONS:
                raise ValueError("active adaptation must target a canonical policy dimension")
            if row["domain"] != "MODEL_HANDLING":
                # ACCEPTED is the confirmation boundary. Effective policy still
                # changes only through CognitivePolicyEngine.observe().
                self.policy_engine.observe(user_id, row["target"], row["proposed_value"],
                    strength=STRONG, evidence=reason, correlation_id=correlation_id)
        stamp=now(); self.db.execute("UPDATE policy_governance SET state=?,updated_at=? WHERE id=? AND user_id=?",(new_state,stamp,candidate_id,user_id))
        self.db.execute("INSERT INTO policy_governance_history (candidate_id,user_id,tenant_id,previous_state,new_state,reason,evidence_refs,correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (candidate_id,user_id,row["tenant_id"],row["state"],new_state,reason[:500],json.dumps(evidence_refs),correlation_id,stamp))
        payload={"candidate_id":candidate_id,"previous_state":row["state"],"new_state":new_state,"reason":reason,"evidence_refs":list(evidence_refs),"tenant_id":row["tenant_id"],"user_id":user_id}
        self.bus.emit(user_id,"governance.transition",f"Policy governance: {row['state']} → {new_state}",subject_kind="policy_governance",subject_id=candidate_id,payload=payload,correlation_id=correlation_id)
        return dict(self.db.query_one("SELECT * FROM policy_governance WHERE id=?",(candidate_id,)))
    def history(self,user_id,candidate_id): return [dict(r) for r in self.db.query("SELECT * FROM policy_governance_history WHERE candidate_id=? AND user_id=? ORDER BY id",(candidate_id,user_id))]

class PolicyRuntimeCoordinator:
    def __init__(self, governance): self.governance=governance
    def run_once(self, user_id: str, correlation_id: str, *, candidate_id: str | None = None) -> dict[str, Any]:
        if not correlation_id: raise ValueError("correlation_id is required")
        if candidate_id: return {"invoked": True, "candidate_id": candidate_id, "status": "bounded_no_auto_accept"}
        return {"invoked": True, "status": "no_candidate"}
