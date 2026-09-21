"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { PortabilityExport, PortabilityImport, PortabilityPlan, RestoreConflict, RestoreOperation } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

/** Real V8.4.4 export/import/recovery surface. Every state comes from the API. */
export default function PortabilityPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [exports, setExports] = useState<PortabilityExport[]>([]);
  const [imports, setImports] = useState<PortabilityImport[]>([]);
  const [history, setHistory] = useState<RestoreOperation[]>([]);
  const [selectedImport, setSelectedImport] = useState<string | null>(null);
  const [plan, setPlan] = useState<PortabilityPlan | null>(null);
  const [conflicts, setConflicts] = useState<RestoreConflict[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [e, i, h] = await Promise.all([api.portabilityExports(), api.portabilityImports(), api.portabilityHistory()]);
      setExports(e.exports); setImports(i.imports); setHistory(h.operations);
      setMessage(null);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Portability backend unavailable."); }
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);

  const create = async () => {
    setBusy(true); setMessage(null);
    try { await api.createPortabilityExport(); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Export failed."); }
    finally { setBusy(false); }
  };

  const stage = async (file: File) => {
    setBusy(true); setMessage(null);
    try {
      const staged = await api.stagePortabilityImport(file);
      const id = staged.import.id;
      setSelectedImport(id);
      const validated = await api.validatePortabilityImport(id);
      if (validated.validation?.status === "VALIDATED") {
        setMessage("Package validated. Run a dry-run before any restore.");
      } else {
        setMessage(`Package rejected: ${(validated.validation?.errors || []).join("; ")}`);
      }
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Import failed."); }
    finally { setBusy(false); }
  };

  const dryRun = async () => {
    if (!selectedImport) return;
    setBusy(true); setMessage(null);
    try {
      const result = await api.dryRunPortabilityRestore(selectedImport);
      setPlan(result.plan);
      setConflicts(result.plan.conflicts || []);
      setMessage(result.plan.status === "READY" ? "Dry-run ready for explicit confirmation." : "Restore is blocked until every conflict is resolved.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Dry-run failed."); }
    finally { setBusy(false); }
  };

  const restore = async () => {
    if (!selectedImport || !plan || plan.status !== "READY") return;
    const confirmed = window.confirm("Apply this restore? Existing records will not be overwritten unless each conflict has an explicit replace choice.");
    if (!confirmed) return;
    setBusy(true); setMessage(null);
    try { const result = await api.restorePortability(selectedImport, true); setMessage(`Restore ${result.operation.status.toLowerCase()}.`); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Restore blocked or failed; live state was not assumed to change."); }
    finally { setBusy(false); }
  };

  return (
    <Panel title="Data portability & recovery" hint="V8.4.4 · real packages, validation and rollback-safe restore">
      <div style={{ display: "grid", gap: "1.25rem" }}>
        {message && <p className="body" style={{ margin: 0, color: message.toLowerCase().includes("failed") || message.toLowerCase().includes("reject") ? "#e88a7a" : "var(--accent)" }}>{message}</p>}
        <section>
          <p className="label">Export</p>
          <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap", marginTop: "0.7rem" }}>
            <button className="btn btn-primary" onClick={() => void create()} disabled={busy}>{busy ? "Working…" : "Create package"}</button>
            {exports[0] && <a className="btn" href={`/api/portability/v1/exports/${encodeURIComponent(exports[0].id)}/download`}>Download latest</a>}
          </div>
          {exports[0] ? <div style={{ marginTop: "0.8rem" }}><Row label="Latest" value={exports[0].id} /><Row label="Integrity" value={<StateBadge value={exports[0].integrity?.status || "UNKNOWN"} />} /><Row label="Objects" value={Object.values(exports[0].object_counts || {}).reduce((a, b) => a + b, 0)} /></div> : <Empty>No export package recorded.</Empty>}
        </section>
        <section>
          <p className="label">Import / restore</p>
          <input ref={input} type="file" accept=".zip,application/zip" className="field" style={{ marginTop: "0.7rem" }} onChange={(event) => { const file = event.target.files?.[0]; if (file) void stage(file); }} disabled={busy} />
          {imports.length > 0 && <select className="field" style={{ marginTop: "0.7rem" }} value={selectedImport || ""} onChange={(event) => { setSelectedImport(event.target.value); setPlan(null); }}><option value="">Select staged package</option>{imports.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.state}</option>)}</select>}
          {selectedImport && <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", marginTop: "0.7rem" }}><button className="btn" onClick={() => void api.validatePortabilityImport(selectedImport).then(() => load())} disabled={busy}>Validate again</button><button className="btn" onClick={() => void dryRun()} disabled={busy}>Dry-run restore</button><button className="btn btn-primary" onClick={() => void restore()} disabled={busy || !plan || plan.status !== "READY"}>Confirm restore</button></div>}
          {plan && <div className="panel" style={{ padding: "0.9rem", marginTop: "0.8rem" }}><Row label="Plan" value={<StateBadge value={plan.status} />} /><Row label="Records" value={plan.records} /><Row label="New" value={plan.inserts} /><Row label="Updates" value={plan.updates} /><Row label="Conflicts" value={plan.conflicts?.length || 0} />{(plan.blockers?.length || 0) > 0 && <p className="body" style={{ color: "#e88a7a", fontSize: "0.78rem", margin: "0.7rem 0 0" }}>Blocked: explicit conflict decisions are required.</p>}</div>}
          {conflicts.length > 0 && <ul style={{ margin: "0.8rem 0 0", paddingLeft: "1.1rem" }}>{conflicts.slice(0, 5).map((conflict) => <li key={conflict.id} className="body" style={{ fontSize: "0.78rem" }}>{conflict.table_name}: {conflict.state} — {conflict.reason}</li>)}</ul>}
        </section>
        <section><p className="label">Recovery history</p>{history.length ? history.slice(0, 4).map((operation) => <Row key={operation.id} label={operation.id} value={<StateBadge value={operation.status} />} />) : <Empty>No restore operations recorded.</Empty>}</section>
      </div>
    </Panel>
  );
}
