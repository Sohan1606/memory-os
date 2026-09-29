"use client";
/**
 * ZORQ Devices & Connectivity surface (Z-UI.1 WP-UI-6).
 * Today's truth: one current device, LOCAL connection, NOT-CONFIGURED sync.
 * The state vocabularies render as contracts for future phases — never as
 * simulated active states. Device trust, sync, and authorization are always
 * distinct displays.
 */
import {
  ErrorPanel, LoadingPanel, NotImplementedPanel, PageHeader, Readout,
  UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { zorqApi } from "@/lib/api";
import type { ConnectionState, ExecutionOrigin, SyncState } from "@/lib/types";

const VOCAB_RENDER: {
  family: string; note: string;
  values: { v: ConnectionState | ExecutionOrigin | SyncState; note?: string }[];
}[] = [
  {
    family: "Connection",
    note: "Per surface/session. LOCAL is the truthful state today; OFFLINE/DEGRADED/UNKNOWN exist in the contract and will never render as healthy.",
    values: [
      { v: "LOCAL", note: "True today — served by the local runtime." },
      { v: "ONLINE" }, { v: "OFFLINE" }, { v: "DEGRADED" },
      { v: "UNKNOWN", note: "Never rendered as healthy." },
      { v: "UNAVAILABLE" }, { v: "SYNCING" },
    ],
  },
  {
    family: "Execution origin",
    note: "Per action. Where execution happens is explicit; delegation carries scope and expiry.",
    values: [{ v: "LOCAL" }, { v: "REMOTE" }, { v: "DELEGATED" }],
  },
  {
    family: "Synchronization",
    note: "Per data class/device. NOT-CONFIGURED is the truthful state until Z-DIST.1 exists.",
    values: [
      { v: "SYNCED" }, { v: "SYNCING" }, { v: "PENDING", note: "Offline-queued governed operation awaiting sync." },
      { v: "CONFLICT", note: "Never silently resolved in the UI." },
      { v: "FAILED" }, { v: "NOT-CONFIGURED", note: "True today." },
    ],
  },
];

/** Distinct-authority contracts — the same distinctions the facade returns;
 * they render regardless of facade availability. */
const DISTINCTIONS = [
  "DEVICE TRUST ≠ USER AUTHORIZATION",
  "SYNC ≠ AUTHORIZATION",
  "DEVICE CAPABILITY ≠ USER PERMISSION",
];

const AUTHORIZED_NOTE = "No device enrollment exists yet; this list is truthfully empty.";
const AVAILABLE_NOTE = "No discovery mechanism exists yet; this list is truthfully empty. Discovery would not be permission.";

export default function DevicesPage() {
  const devices = useFacade(zorqApi.devices, 15_000);
  const data = devices.data?.available ? devices.data : null;

  return (
    <div className="z-page">
      <PageHeader
        kicker="Runtime · Devices"
        title="Devices & connectivity"
        lede="ZORQ is designed to operate across authorized devices. Today there is exactly one — this one — and no synchronization. Everything on this page is the truthful current state plus the contracts future phases will fill in."
      />

      {devices.condition === "LOADING" && <LoadingPanel rows={4} />}
      {devices.condition === "ERROR" && <ErrorPanel retry={devices.refresh} />}
      {devices.condition === "UNAVAILABLE" && devices.reason && <UnavailablePanel reason={devices.reason} retry={devices.refresh} />}

      <div className="z-grid" data-cols="2">
          {data && (
          <ZPanel title="Current device" hint="Real identity of the device this runtime composes on.">
            <Readout k="Device" muted>{data.current_device.device_id}</Readout>
            <Readout k="Class" muted>{data.current_device.device_class}</Readout>
            <Readout k="Trust tier" muted>{data.current_device.trust_tier}</Readout>
            <Readout k="Agent" muted>v{data.current_device.agent_version}</Readout>
            <Readout k="Attested"><StateToken value={data.current_device.attested ? "VERIFIED" : "UNVERIFIED"} /></Readout>
            <Readout k="Connection"><StateToken value={data.connection} /></Readout>
            <Readout k="Execution origin"><StateToken value={data.execution_origin} /></Readout>
            <Readout k="Synchronization"><StateToken value={data.sync} note={data.sync_note} /></Readout>
          </ZPanel>
          )}

          <ZPanel title="Authorized devices" hint={data?.authorized_devices_note ?? AUTHORIZED_NOTE}>
            <div className="z-condition" role="note">
              <span className="z-condition-label">None enrolled</span>
              <p className="z-condition-note">
                No device enrollment exists. Enrollment — not trust alone, and never
                discovery — is what will place a device in this list.
              </p>
            </div>
          </ZPanel>

          <ZPanel title="Available devices" hint={data?.available_devices_note ?? AVAILABLE_NOTE}>
            <div className="z-condition" role="note">
              <span className="z-condition-label">None discovered</span>
              <p className="z-condition-note">
                No discovery mechanism exists. Even when it does, a discovered device
                grants nothing: discovery is not permission.
              </p>
            </div>
          </ZPanel>

          <ZPanel title="Distinct authorities" hint="These are separate facts, rendered separately. None implies another.">
            <div style={{ display: "grid", gap: "0.6rem" }}>
              {(data?.distinctions ?? DISTINCTIONS).map((d) => (
                <p key={d} style={{
                  margin: 0, fontFamily: "var(--mono)", fontSize: "0.66rem",
                  letterSpacing: "0.1em", color: "var(--z-ink-2)",
                  border: "1px solid var(--z-line)", borderRadius: 4, padding: "0.6rem 0.75rem",
                }}>{d}</p>
              ))}
            </div>
          </ZPanel>

          <ZPanel title="State vocabularies" hint="The full contracts (§12.1.1). Rendered as contracts — nothing simulated as active." wide>
            <div style={{ display: "grid", gap: "1.1rem" }}>
              {VOCAB_RENDER.map((family) => (
                <div key={family.family}>
                  <p className="z-nav-group" style={{ marginBottom: "0.45rem" }}>{family.family}</p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem", marginBottom: "0.4rem" }}>
                    {family.values.map((item) => (
                      <StateToken key={item.v} value={item.v} small note={item.note} />
                    ))}
                  </div>
                  <p style={{ margin: 0, fontSize: "0.74rem", color: "var(--z-ink-3)", lineHeight: 1.5 }}>{family.note}</p>
                </div>
              ))}
            </div>
          </ZPanel>

          <ZPanel title="Offline-first future" hint="What is coming, and what is not faked now." wide>
            <NotImplementedPanel what="The local-first runtime (offline intelligence)" phase="Z-LD.1" />
            <div style={{ height: "0.7rem" }} />
            <NotImplementedPanel what="Synchronization and multi-device operation" phase="Z-DIST.1" />
          </ZPanel>
      </div>
    </div>
  );
}
