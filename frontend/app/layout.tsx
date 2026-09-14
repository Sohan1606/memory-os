import type { Metadata, Viewport } from "next";

import CustomCursor from "@/components/CustomCursor";
import MemoryInspector from "@/components/MemoryInspector";
import Navigation from "@/components/Navigation";
import { MemoryStoreProvider } from "@/hooks/useMemoryStore";

import "./globals.css";

export const metadata: Metadata = {
  title: "MEMORY//OS — An AI assistant that actually remembers",
  description:
    "A local-first AI agent with persistent long-term memory: LangGraph, LangChain tools, ChromaDB and local embeddings, presented as a cinematic product study.",
  applicationName: "MEMORY//OS",
  authors: [{ name: "MEMORY//OS" }],
  openGraph: {
    title: "MEMORY//OS",
    description: "An AI assistant that actually remembers.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#050506",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">Skip to content</a>
        <MemoryStoreProvider>
          <CustomCursor />
          <Navigation />
          <main id="main">{children}</main>
          <MemoryInspector />
        </MemoryStoreProvider>
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}
