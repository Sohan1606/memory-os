"use client";
/**
 * V8.4.3 Connected Research surface.
 *
 * There is no search engine here — the user (or the assistant, when told an
 * explicit URL) fetches real http(s) pages through an SSRF-defended
 * pipeline. This panel is deliberately built around four distinct visual
 * registers so a SOURCE (a raw fetched page), an EVIDENCE excerpt (a
 * sentence pulled from that page), a CLAIM (a bounded-confidence statement
 * derived from evidence) and a WORLD UPDATE (an explicit, confirmed change
 * to what the system believes) never look like the same kind of thing — and
 * a failed/blocked fetch never renders like "nothing found".
 */
import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type {
  ResearchClaim, ResearchConflict, ResearchEvidence, ResearchFetch,
  ResearchSession, ResearchSource, ResearchStatus, ResearchWorldUpdate,
} from "@/lib/types";
import { Empty, Meter, Panel, Row, StateBadge } from "./primitives";

function fetchStateTone(status: string): string {
  if (status === "COMPLETED") return "RELIABLE";
  if (status === "BLOCKED") return "CONTRADICTED";
  if (status === "TIMEOUT" || status === "FETCH_FAILED") return "AT RISK";
  return status;
}

export default function ResearchPanel({ refreshKey }: { refreshKey: number }) {
  const [status, setStatus] = useState<ResearchStatus | null>(null);
  const [sessions, setSessions] = useState<ResearchSession[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [fetches, setFetches] = useState<ResearchFetch[]>([]);
  const [evidence, setEvidence] = useState<ResearchEvidence[]>([]);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  const [conflicts, setConflicts] = useState<ResearchConflict[]>([]);
  const [worldUpdates, setWorldUpdates] = useState<ResearchWorldUpdate[]>([]);
  const [question, setQuestion] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.researchStatus().then((s) => { if (!cancelled) setStatus(s); }).catch(() => {});
    api.researchSessions()
      .then((d) => { if (!cancelled) setSessions(d.sessions); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Research unavailable."); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  const loadSession = (id: string) => {
    setSelected(id);
    setNotice(null);
    Promise.all([
      api.researchSession(id),
      api.researchSources(id),
      api.researchFetches(id),
      api.researchEvidence(id),
      api.researchClaims(id),
      api.researchConflicts(id),
      api.researchWorldUpdates(id),
    ]).then(([s, src, fe, ev, cl, cf, wu]) => {
      setSession(s.session);
      setSources(src.sources);
      setFetches(fe.fetches);
      setEvidence(ev.evidence);
      setClaims(cl.claims);
      setConflicts(cf.conflicts);
      setWorldUpdates(wu.world_updates);
    }).catch((e) => setError(e instanceof Error ? e.message : "Session unavailable."));
  };

  const refreshSelected = () => { if (selected) loadSession(selected); };

  const createSession = async () => {
    if (!question.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createResearchSession(question.trim());
      setQuestion("");
      setSessions((s) => [created.session, ...s]);
      loadSession(created.session.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start research session.");
    } finally {
      setBusy(false);
    }
  };

  const runFetch = async () => {
    if (!selected || !url.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.fetchResearchSource(selected, url.trim());
      setUrl("");
      setNotice(
        result.status === "COMPLETED"
          ? `Fetched successfully. ${result.evidence_created ?? 0} evidence excerpt(s), ` +
            `${result.claims_created_or_updated ?? 0} claim(s) affected.`
          : `${result.status}${result.error_code ? ` (${result.error_code})` : ""}: ${result.detail}`
      );
      refreshSelected();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "Fetch request failed.");
    } finally {
      setBusy(false);
    }
  };

  const finishSession = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.finishResearchSession(selected);
      refreshSelected();
      api.researchSessions().then((d) => setSessions(d.sessions)).catch(() => {});
    } finally {
      setBusy(false);
    }
  };

  const propose = async (claimId: string) => {
    if (!selected) return;
    const label = window.prompt("Label this world update (e.g. \"Launch date\"):");
    if (!label) return;
    setBusy(true);
    try {
      await api.proposeResearchWorldUpdate(selected, claimId, "commitment", label);
      refreshSelected();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "Could not propose world update.");
    } finally {
      setBusy(false);
    }
  };

  const applyUpdate = async (updateId: string) => {
    const ok = window.confirm(
      "Apply this research-derived fact to the World Model? Its confidence " +
      "will remain capped and it will be tagged as sourced from research."
    );
    if (!ok) return;
    setBusy(true);
    try {
      await api.applyResearchWorldUpdate(updateId, true);
      refreshSelected();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "Could not apply world update.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Connected Research"
      hint={status?.detail ?? "Real http(s) fetches of URLs you or the assistant supply. No search engine exists — nothing is invented."}
      right={status ? <StateBadge value={status.available ? "AVAILABLE" : "UNAVAILABLE"} /> : null}
      style={{ gridColumn: "1 / -1" }}
    >
      {error && <Empty>{error}</Empty>}

      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What are you researching? (e.g. When does the product launch?)"
          style={inputStyle}
        />
        <button type="button" disabled={busy || !question.trim()} onClick={createSession} style={btnStyle}>
          Start session
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 260px) 1fr", gap: "1.2rem" }}>
        {/* ---------------------------------------------------- sessions */}
        <div>
          <h4 style={sectionHeading}>Sessions</h4>
          {sessions.length === 0 && <Empty>No research sessions yet. Start one above.</Empty>}
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {sessions.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => loadSession(s.id)}
                  style={{
                    display: "block", width: "100%", textAlign: "left",
                    background: selected === s.id ? "var(--graphite-800)" : "transparent",
                    border: "1px solid var(--line)", borderRadius: 4,
                    padding: "0.5rem 0.6rem", marginBottom: "0.4rem", cursor: "pointer",
                  }}
                >
                  <span style={{ display: "block", fontSize: "0.78rem", color: "var(--warm)", marginBottom: "0.3rem" }}>
                    {s.question}
                  </span>
                  <StateBadge value={s.state} />
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* ------------------------------------------------------ detail */}
        <div>
          {!session && <Empty>Select a session to inspect its sources, evidence and claims.</Empty>}
          {session && (
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              <div>
                <Row label="Question" value={session.question} mono={false} />
                <Row label="State" value={<StateBadge value={session.state} />} />
                <Row label="Sources / Evidence / Claims / Conflicts" value={
                  `${session.source_count} / ${session.evidence_count} / ${session.claim_count} / ${session.conflict_count}`
                } />
                <Row label="Detail" value={session.detail} mono={false} />
              </div>

              {session.state !== "COMPLETED" && session.state !== "FAILED" && (
                <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                  <input
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://an-explicit-url-you-know.example/page"
                    style={inputStyle}
                  />
                  <button type="button" disabled={busy || !url.trim()} onClick={runFetch} style={btnStyle}>
                    Fetch
                  </button>
                  <button type="button" disabled={busy} onClick={finishSession} style={btnStyleGhost}>
                    Finish session
                  </button>
                </div>
              )}
              {notice && (
                <p style={{ fontFamily: "var(--mono)", fontSize: "0.72rem", color: "var(--silver)", margin: 0 }}>
                  {notice}
                </p>
              )}

              {/* Fetch ledger — failures are always shown, never hidden */}
              <div>
                <h4 style={sectionHeading}>Fetch ledger ({fetches.length})</h4>
                {fetches.length === 0 && <Empty>No fetch attempts yet.</Empty>}
                {fetches.map((f) => (
                  <div key={f.id} style={rowCard}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "0.6rem", flexWrap: "wrap" }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: "0.7rem", color: "var(--warm)", wordBreak: "break-all" }}>
                        {f.requested_url}
                      </span>
                      <StateBadge value={fetchStateTone(f.status)} title={f.status} />
                    </div>
                    {f.error_code && (
                      <p style={{ margin: "0.3rem 0 0", fontSize: "0.74rem", color: "#e88a7a" }}>
                        {f.error_code}: {f.error_detail}
                      </p>
                    )}
                    {!f.error_code && (
                      <p style={{ margin: "0.3rem 0 0", fontFamily: "var(--mono)", fontSize: "0.66rem", color: "var(--muted)" }}>
                        HTTP {f.http_status ?? "—"} · {f.bytes_read ?? 0} bytes · {f.latency_ms ?? 0}ms
                        {f.redirect_count > 0 ? ` · ${f.redirect_count} redirect(s)` : ""}
                      </p>
                    )}
                  </div>
                ))}
              </div>

              {/* Sources */}
              <div>
                <h4 style={sectionHeading}>Sources ({sources.length})</h4>
                {sources.length === 0 && <Empty>No sources recorded.</Empty>}
                {sources.map((s) => (
                  <div key={s.id} style={rowCard}>
                    <span style={{ fontSize: "0.8rem", color: "var(--warm)", display: "block" }}>
                      {s.title || s.canonical_url}
                    </span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "0.64rem", color: "var(--muted)" }}>
                      {s.domain} · <StateBadge value={s.availability} />
                    </span>
                  </div>
                ))}
              </div>

              {/* Evidence — deliberately distinct visual register from claims */}
              <div>
                <h4 style={sectionHeading}>Evidence excerpts ({evidence.length})</h4>
                {evidence.length === 0 && <Empty>No evidence extracted yet. Evidence is a raw excerpt, not a belief.</Empty>}
                {evidence.map((e) => (
                  <div key={e.id} style={{ ...rowCard, borderLeft: "2px solid var(--line-strong)" }}>
                    <p style={{ margin: 0, fontSize: "0.8rem", color: "var(--warm)", fontStyle: "italic" }}>
                      &ldquo;{e.excerpt}&rdquo;
                    </p>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--muted)" }}>
                      from {e.locator}
                      {e.injection_flags.length > 0 && (
                        <> · <StateBadge value="INJECTION ATTEMPT FLAGGED (NOT EXECUTED)" /></>
                      )}
                    </span>
                  </div>
                ))}
              </div>

              {/* Claims — bounded confidence, evidence != belief */}
              <div>
                <h4 style={sectionHeading}>Claims ({claims.length})</h4>
                {claims.length === 0 && <Empty>No claims derived yet.</Empty>}
                {claims.map((c) => (
                  <div key={c.id} style={{ ...rowCard, borderLeft: "2px solid var(--accent-line)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "0.6rem", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "0.82rem", color: "var(--warm)" }}>{c.statement}</span>
                      <StateBadge value={c.status} />
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.4rem", flexWrap: "wrap", gap: "0.5rem" }}>
                      <Meter value={c.claim_confidence} label={`claim confidence ${Math.round(c.claim_confidence * 100)}% (capped, externally sourced)`} />
                      <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--muted)" }}>
                        {c.corroboration_count} corroboration(s) · {c.independent_domain_count} independent domain(s)
                      </span>
                    </div>
                    {c.status !== "contested" && (
                      <button type="button" onClick={() => propose(c.id)} disabled={busy} style={{ ...btnStyleGhost, marginTop: "0.5rem" }}>
                        Propose world update
                      </button>
                    )}
                  </div>
                ))}
              </div>

              {/* Conflicts — both sides always preserved */}
              {conflicts.length > 0 && (
                <div>
                  <h4 style={sectionHeading}>Conflicts ({conflicts.length})</h4>
                  {conflicts.map((cf) => (
                    <div key={cf.id} style={{ ...rowCard, borderLeft: "2px solid #e88a7a" }}>
                      <p style={{ margin: 0, fontSize: "0.78rem", color: "#e88a7a" }}>{cf.reason}</p>
                      <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--muted)" }}>
                        {cf.claim_ids.length} conflicting claim(s) — both preserved, neither silently discarded.
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* World updates — explicit, confirmed only */}
              <div>
                <h4 style={sectionHeading}>World updates ({worldUpdates.length})</h4>
                {worldUpdates.length === 0 && <Empty>No world updates proposed. Research evidence never changes the World Model by itself.</Empty>}
                {worldUpdates.map((wu) => (
                  <div key={wu.id} style={rowCard}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "0.6rem", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "0.8rem", color: "var(--warm)" }}>{wu.label}</span>
                      <StateBadge value={wu.state} />
                    </div>
                    <Meter value={wu.applied_confidence ?? wu.proposed_confidence} label={`${wu.state === "APPLIED" ? "applied" : "proposed"} confidence (capped)`} />
                    {wu.state === "PROPOSED" && (
                      <button type="button" onClick={() => applyUpdate(wu.id)} disabled={busy} style={{ ...btnStyle, marginTop: "0.5rem" }}>
                        Confirm &amp; apply to World Model
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

const sectionHeading: React.CSSProperties = {
  fontFamily: "var(--mono)", fontSize: "0.64rem", letterSpacing: "0.1em",
  textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.5rem",
};

const rowCard: React.CSSProperties = {
  background: "var(--graphite-800)", border: "1px solid var(--line)",
  borderRadius: 4, padding: "0.55rem 0.7rem", marginBottom: "0.4rem",
};

const inputStyle: React.CSSProperties = {
  flex: 1, minWidth: 220, background: "var(--graphite-900)",
  border: "1px solid var(--line-strong)", borderRadius: 4,
  padding: "0.5rem 0.7rem", color: "var(--warm)", fontSize: "0.82rem",
};

const btnStyle: React.CSSProperties = {
  background: "var(--accent-dim)", border: "1px solid var(--accent-line)",
  borderRadius: 4, color: "var(--accent)", fontFamily: "var(--mono)",
  fontSize: "0.68rem", letterSpacing: "0.06em", padding: "0.5rem 0.8rem",
  cursor: "pointer",
};

const btnStyleGhost: React.CSSProperties = {
  background: "transparent", border: "1px solid var(--line-strong)",
  borderRadius: 4, color: "var(--silver)", fontFamily: "var(--mono)",
  fontSize: "0.68rem", letterSpacing: "0.06em", padding: "0.5rem 0.8rem",
  cursor: "pointer",
};
