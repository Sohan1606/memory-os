"use client";
/**
 * ZORQ intelligence rail — the signature interaction pattern (Z-UI.1 final
 * semantic refinement). Two strictly separated flows:
 *
 *   INTELLIGENCE   OBSERVE → UNDERSTAND → ANALYZE → DECIDE
 *   ACTION GATE    PROPOSE ∥ AUTHORIZE → ACT → VERIFY
 *
 * The ∥ gate is the authority boundary. Cognition (a model call, a normal
 * answer, a memory write, a tool decision) NEVER visually implies that an
 * action was proposed, authorized or executed.
 *
 * Evidence rules (each stage lights only from an explicit, corresponding
 * backend event in the turn's activity list):
 *   - DECIDE   is evidenced only by a decision event the backend explicitly
 *              reports (TOOL_DECISION — which tool to invoke). A generated
 *              response alone never evidences DECIDE.
 *   - PROPOSE  is evidenced ONLY by an actual action-proposal record
 *              (ACTION_PROPOSED). MODEL_CALL, MODEL_REVISION, DEMO_PLANNER,
 *              SAVE_MEMORY, MEMORY_MANAGER and ordinary response generation
 *              are NOT proposal evidence — a memory save is a memory
 *              operation, not an external action proposal. No chat activity
 *              type in the current backend vocabulary represents an action
 *              proposal, so PROPOSE is honestly dormant today.
 *   - AUTHORIZE / ACT / VERIFY are evidenced only by authorization,
 *              execution and verification records — none exist in chat
 *              activity today, so the action lifecycle stays visibly closed.
 */
import type { ChatActivity } from "@/lib/types";

export type RailStage =
  | "OBSERVE" | "UNDERSTAND" | "ANALYZE" | "DECIDE"
  | "PROPOSE" | "AUTHORIZE" | "ACT" | "VERIFY";

export interface RailTurn {
  activity: ChatActivity[];
  recalledCount: number;
  busy: boolean;
}

const INTELLIGENCE_STAGES: { stage: RailStage; meaning: string }[] = [
  { stage: "OBSERVE", meaning: "Input received — the turn was observed." },
  { stage: "UNDERSTAND", meaning: "Thread context assembled and memory consulted." },
  { stage: "ANALYZE", meaning: "Tools and retrieval executed over real data." },
  { stage: "DECIDE", meaning: "A decision the backend explicitly reports (tool selection). Response generation alone never evidences this." },
];

const ACTION_STAGES: { stage: RailStage; meaning: string }[] = [
  { stage: "PROPOSE", meaning: "An actual action proposal recorded by the backend — never a normal reply, model call or memory write." },
  { stage: "AUTHORIZE", meaning: "Explicit owner authorization — the boundary nothing crosses on its own." },
  { stage: "ACT", meaning: "Execution under a one-use lease on an authorized device." },
  { stage: "VERIFY", meaning: "Outcome established from evidence — never assumed from completion." },
];

/* Intelligence-flow evidence. */
const UNDERSTAND_EVIDENCE = new Set(["LOAD_CONTEXT", "CONTEXT_BUILD", "MEMORY_PRELOAD"]);
const ANALYZE_EVIDENCE = new Set([
  "MEMORY_PRELOAD", "SEARCH_MEMORY", "TOOL_SURFACE",
  "MODEL_CALL", "DEMO_PLANNER", "TOOL_RESULT", "TOOL_FAILED",
]);
/** An explicit decision record. Not response generation. */
const DECIDE_EVIDENCE = new Set(["TOOL_DECISION"]);

/*
 * Action-lifecycle evidence. These sets define what WOULD evidence each
 * stage. The current chat activity vocabulary emits NONE of these types, so
 * PROPOSE/AUTHORIZE/ACT/VERIFY render dormant — honestly, never fabricated.
 */
const PROPOSE_EVIDENCE = new Set(["ACTION_PROPOSED", "PROPOSE_ACTION", "ACTION_PROPOSAL"]);
const AUTHORIZE_EVIDENCE = new Set(["ACTION_AUTHORIZED", "AUTHORIZATION_GRANTED"]);
const ACT_EVIDENCE = new Set(["ACTION_EXECUTED", "EXECUTION_STARTED"]);
const VERIFY_EVIDENCE = new Set(["ACTION_VERIFIED", "VERIFICATION_RECORDED"]);

function stageState(stage: RailStage, turn: RailTurn | null): {
  state: "evidenced" | "working" | "dormant"; note: string;
} {
  if (!turn) {
    return { state: "dormant", note: "Awaiting input — no turn has occurred." };
  }
  const types = turn.activity.map((a) => a.type);
  switch (stage) {
    case "OBSERVE":
      return turn.busy
        ? { state: "working", note: "Turn in flight — observed, work under way." }
        : { state: "evidenced", note: "Input received and processed." };
    case "UNDERSTAND":
      return types.some((t) => UNDERSTAND_EVIDENCE.has(t))
        ? { state: "evidenced", note: "Context loaded and memory consulted this turn." }
        : { state: "dormant", note: "No context-assembly evidence reported this turn." };
    case "ANALYZE": {
      const analyzed = types.some((t) => ANALYZE_EVIDENCE.has(t));
      if (turn.busy) return { state: "working", note: "Analyzing — evidence pending." };
      return analyzed
        ? { state: "evidenced", note: turn.recalledCount > 0 ? `${turn.recalledCount} memor${turn.recalledCount === 1 ? "y" : "ies"} recalled with provenance.` : "Tools executed over real data." }
        : { state: "dormant", note: "No tool or retrieval evidence reported this turn." };
    }
    case "DECIDE": {
      if (turn.busy) return { state: "dormant", note: "Awaiting an explicit decision record." };
      const decided = types.some((t) => DECIDE_EVIDENCE.has(t));
      return decided
        ? { state: "evidenced", note: "The backend explicitly reports a decision this turn (tool selection)." }
        : { state: "dormant", note: "No explicit decision event reported — a generated response alone never evidences this." };
    }
    case "PROPOSE": {
      const proposed = types.some((t) => PROPOSE_EVIDENCE.has(t));
      return proposed
        ? { state: "evidenced", note: "An actual action proposal recorded by the backend." }
        : { state: "dormant", note: "No action-proposal record this turn. Ordinary replies, model calls and memory writes are not action proposals." };
    }
    case "AUTHORIZE": {
      const authorized = types.some((t) => AUTHORIZE_EVIDENCE.has(t));
      return authorized
        ? { state: "evidenced", note: "Authorization recorded by the backend." }
        : { state: "dormant", note: "No authorization record — the gate stays closed." };
    }
    case "ACT": {
      const acted = types.some((t) => ACT_EVIDENCE.has(t));
      return acted
        ? { state: "evidenced", note: "Execution recorded by the backend." }
        : { state: "dormant", note: "Nothing executed — no authorized action existed." };
    }
    case "VERIFY": {
      const verified = types.some((t) => VERIFY_EVIDENCE.has(t));
      return verified
        ? { state: "evidenced", note: "Verification recorded by the backend." }
        : { state: "dormant", note: "Nothing to verify — execution was never claimed." };
    }
  }
}

function RailChip({ stage, meaning, turn }: { stage: RailStage; meaning: string; turn: RailTurn | null }) {
  const after = stageState(stage, turn);
  return (
    <span
      className="z-rail-stage"
      data-state={after.state}
      data-pulse={after.state === "working" ? "true" : undefined}
      title={`${meaning}${after.state === "dormant" ? " — dormant: rendered only with backend evidence." : ""}\n${after.note}`}
    >
      <span className="z-rail-marker" aria-hidden="true" />
      <span className="z-rail-label">{stage}</span>
    </span>
  );
}

export default function IntelligenceRail({ turn }: { turn: RailTurn | null }) {
  return (
    <div className="z-rail" role="group" aria-label="Intelligence flow and action gate — evidenced stages only">
      <div className="z-rail-track">
        <span className="z-rail-group" data-flow="intelligence">INTELLIGENCE</span>
        {INTELLIGENCE_STAGES.map(({ stage, meaning }, i) => (
          <span key={stage} className="z-rail-item" data-flow="intelligence">
            <RailChip stage={stage} meaning={meaning} turn={turn} />
            {i < INTELLIGENCE_STAGES.length - 1 && <span className="z-rail-link" aria-hidden="true" />}
          </span>
        ))}

        <span className="z-rail-group" data-flow="action">ACTION GATE</span>
        {ACTION_STAGES.map(({ stage, meaning }, i) => (
          <span key={stage} className="z-rail-item" data-flow="action">
            {i === 1 && (
              <span className="z-rail-gate" title="Authority boundary — cognition, UI intent, model confidence and memory retrieval never cross this line.">∥</span>
            )}
            <RailChip stage={stage} meaning={meaning} turn={turn} />
            {i < ACTION_STAGES.length - 1 && <span className="z-rail-link" aria-hidden="true" />}
          </span>
        ))}
      </div>
      <p className="z-rail-note">
        {turn
          ? turn.busy
            ? "Turn in flight — stages fill as the backend reports evidence. Cognition never implies authorization or execution."
            : "This turn's evidence, exactly as the backend reported it. The action lifecycle opens only for real action records — an ordinary reply or a memory write is not an action proposal."
          : "Intelligence flow and action gate. Stages light only with backend evidence — cognition never implies authorization or execution, and nothing is fabricated."}
      </p>
    </div>
  );
}
