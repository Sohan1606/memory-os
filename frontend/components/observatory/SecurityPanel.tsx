"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  AuthSessionResponse, RateLimitState, SecurityEvent,
} from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

/**
 * V8.5 production-trust surface: who is authenticated, the session state,
 * recent security/audit events (from the canonical EventBus) and the rate
 * limiter's honest state.
 *
 * Renders only identifiers and coarse metadata the backend provides — never
 * tokens, never credentials, never raw cognitive content. In local
 * single-user mode it states that authentication is not enabled instead of
 * pretending a user is "logged in".
 */
export default function SecurityPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [session, setSession] = useState<AuthSessionResponse | null>(null);
  const [events, setEvents] = useState<SecurityEvent[] | null>(null);
  const [rateLimit, setRateLimit] = useState<RateLimitState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const s = await api.authSession();
        if (!active) return;
        setSession(s); setError(null);
        // Admin surfaces need security.read; a 403 is an honest state here,
        // not an error — members simply do not see tenant audit data.
        try {
          const [ev, rl] = await Promise.all([
            api.securityEvents(), api.rateLimitState(),
          ]);
          if (!active) return;
          setEvents(ev.events); setRateLimit(rl);
        } catch {
          if (!active) return;
          setEvents(null); setRateLimit(null);
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Unavailable");
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 20000);
    return () => { active = false; window.clearInterval(timer); };
  }, [refreshKey]);

  if (error) return <Panel title="Security"><Empty>{error}</Empty></Panel>;
  if (!session) return <Panel title="Security"><Empty>Checking session…</Empty></Panel>;

  const authOn = session.auth_mode === "required";

  return (
    <Panel
      title="Security"
      hint={authOn
        ? "Authenticated session, audit trail and rate limiting."
        : "Local single-user mode. No authentication is enforced."}
      right={<StateBadge value={authOn ? "ACTIVE" : "NOT CONFIGURED"} />}
    >
      <Row label="Auth mode" value={session.auth_mode.toUpperCase()} />
      {session.user ? (
        <>
          <Row label="User" value={session.user.display_name} />
          <Row label="Email" value={session.user.email} />
          <Row label="Role" value={<StateBadge value={session.user.role.toUpperCase()} />} />
          <Row label="Workspace" value={session.user.tenant_id} />
        </>
      ) : (
        <Row label="User" value={authOn ? "NOT SIGNED IN" : "LOCAL"} />
      )}

      {rateLimit && (
        <>
          <h4 style={{
            fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
            textTransform: "uppercase", color: "var(--muted)",
            margin: "1.1rem 0 0.4rem",
          }}>
            Rate limiting
          </h4>
          <Row
            label="State"
            value={<StateBadge value={rateLimit.enabled ? "ACTIVE" : "NOT CONFIGURED"} />}
          />
          {Object.entries(rateLimit.limits_per_minute).map(([category, limit]) => (
            <Row key={category} label={category} value={`${limit}/min`} />
          ))}
        </>
      )}

      {events && (
        <>
          <h4 style={{
            fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
            textTransform: "uppercase", color: "var(--muted)",
            margin: "1.1rem 0 0.4rem",
          }}>
            Recent security events
          </h4>
          {events.length === 0 ? (
            <Empty>No security events recorded.</Empty>
          ) : (
            events.slice(0, 8).map((event) => (
              <Row
                key={event.id}
                label={event.type}
                value={event.created_at.slice(0, 19).replace("T", " ")}
              />
            ))
          )}
        </>
      )}
      {authOn && !events && session.user && (
        <Empty>Audit review requires an admin role.</Empty>
      )}
    </Panel>
  );
}
