import Link from "next/link";

import Footer from "@/components/Footer";
import Gallery from "@/components/Gallery";
import Hero from "@/components/Hero";
import LayerSwitcher from "@/components/LayerSwitcher";
import RetrievalDemo from "@/components/RetrievalDemo";
import ScrollSequence from "@/components/ScrollSequence";
import Section from "@/components/Section";
import VoiceDemo from "@/components/VoiceDemo";

export default function HomePage() {
  return (
    <>
      <Hero />

      <Section
        id="problem"
        index="01"
        label="The problem"
        title={<>Every conversation starts from zero.</>}
        lede="You explain your stack, your preferences and your constraints. The session ends and all of it is gone. The next conversation begins with the same introductions, the same corrections, the same context you already paid to provide."
      >
        <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
          {[
            ["Context resets", "Nothing survives the session boundary, so nothing compounds."],
            ["Repetition tax", "The same preferences are re-stated in every single thread."],
            ["No provenance", "When an assistant claims to know you, it cannot show why."],
          ].map(([t, d]) => (
            <div key={t} className="panel" style={{ padding: "1.4rem" }}>
              <p className="subhead" style={{ fontSize: "1.15rem" }}>{t}</p>
              <p className="body" style={{ marginTop: "0.6rem", fontSize: "0.9375rem" }}>{d}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Signature scroll-linked canvas sequence — used exactly once. */}
      <ScrollSequence />

      <Section
        id="layers"
        index="02"
        label="Memory layers"
        title={<>Memory is not one bucket.</>}
        lede="Short-term thread state and long-term user knowledge are stored differently and retrieved differently. Every count below is read from the live database."
        wide
      >
        <LayerSwitcher />
      </Section>

      <Section
        id="retrieval"
        index="03"
        label="Retrieval"
        title={<>Recall you can interrogate.</>}
        lede="Ask a question and watch the real pipeline run: embedding, vector search, hybrid ranking, context assembly. Each match reports the reasons it was selected, and the system says so plainly when nothing is relevant."
        wide
      >
        <RetrievalDemo />
      </Section>

      <Section
        id="voice"
        index="04"
        label="Voice"
        title={<>Speak it once.</>}
        lede="Speech is transcribed in the browser and enters exactly the same memory pipeline as typed input — classification, duplicate detection, conflict resolution and all."
      >
        <VoiceDemo />
      </Section>

      <Section
        id="surfaces"
        index="05"
        label="Product surfaces"
        title={<>The whole system, end to end.</>}
        wide
      >
        <Gallery />
      </Section>

      <Section
        id="stack"
        index="06"
        label="The stack"
        title={<>Real infrastructure, no API keys.</>}
        lede="ChromaDB with local MiniLM embeddings, SQLite for metadata and audit history, LangGraph for the agent loop, LangChain structured tools for memory operations. The demo mode requires no paid service."
      >
        <div style={{ display: "flex", gap: "0.7rem", flexWrap: "wrap" }}>
          <Link href="/architecture" className="btn btn-primary" data-cursor="cta">
            See the architecture
          </Link>
          <Link href="/memory" className="btn">Browse stored memory</Link>
        </div>
      </Section>

      <Footer />
    </>
  );
}
