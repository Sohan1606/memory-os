import type { Metadata } from "next";

import Architecture from "@/components/Architecture";
import Footer from "@/components/Footer";
import Section from "@/components/Section";

export const metadata: Metadata = {
  title: "Architecture — MEMORY//OS",
  description: "How MEMORY//OS turns a conversation into durable, explainable memory.",
};

export default function ArchitecturePage() {
  return (
    <>
      <Section
        index="—"
        label="Architecture"
        title={<>How a sentence becomes a memory.</>}
        lede="Seven layers, each one implemented in this repository. Where a layer's state can be measured at runtime, it is reported live from the health endpoint rather than described."
        wide
      >
        <Architecture />
      </Section>

      <Section index="—" label="Honest status" title={<>What is actually running.</>} wide>
        <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
          {[
            ["Always active", "LangGraph StateGraph, LangChain structured tools, ChromaDB, local MiniLM embeddings, SQLite metadata and audit log."],
            ["Optional", "Ollama or OpenAI providers for true model-driven tool calling; faster-whisper for server-side transcription. Absent by default and reported as such."],
            ["Never faked", "If a component is not configured, the interface says NOT CONFIGURED. It does not simulate the capability."],
          ].map(([t, d]) => (
            <div key={t} className="panel" style={{ padding: "1.4rem" }}>
              <p className="label label-accent">{t}</p>
              <p className="body" style={{ marginTop: "0.7rem", fontSize: "0.9375rem" }}>{d}</p>
            </div>
          ))}
        </div>
      </Section>

      <Footer />
    </>
  );
}
