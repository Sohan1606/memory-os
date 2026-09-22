"use client";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { CognitiveObject, MeaningCompilation, PersonalState } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

export default function SemanticStatePanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [state, setState] = useState<PersonalState | null>(null);
  const [objects, setObjects] = useState<CognitiveObject[]>([]);
  const [compilations, setCompilations] = useState<MeaningCompilation[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.personalState(), api.cognitiveObjects(), api.meaningCompilations()])
      .then(([personal, cognitive, meanings]) => {
        if (cancelled) return;
        setState(personal); setObjects(cognitive.objects);
        setCompilations(meanings.compilations); setError(false);
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  return (
    <Panel title="V9 semantic state" hint="Validated meaning, canonical objects and reconstructable personal-state versions. No private reasoning is shown.">
      {error ? <Empty>Semantic state is unavailable.</Empty> : (
        <>
          <Row label="State version" value={state?.version ?? 0} />
          <Row label="Active objects" value={objects.filter((o) => o.status === "ACTIVE").length} />
          <Row label="Compilations" value={compilations.length} />
          {objects.slice(0, 4).map((object) => (
            <div key={object.id} style={{ borderTop: "1px solid var(--line)", padding: "0.65rem 0" }}>
              <StateBadge value={`${object.type} · ${object.modality}`} />
              <p style={{ margin: "0.4rem 0 0", color: "var(--warm)", fontSize: "0.8rem", lineHeight: 1.5 }}>
                {object.content}
              </p>
              <p className="mono" style={{ margin: "0.25rem 0 0", color: "var(--muted)", fontSize: "0.58rem" }}>
                {object.provenance} · confidence {object.confidence.toFixed(2)}
              </p>
            </div>
          ))}
          {objects.length === 0 && <Empty>No material semantic state has been recorded.</Empty>}
        </>
      )}
    </Panel>
  );
}
