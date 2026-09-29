import type { Metadata, Viewport } from "next";

import MemoryInspector from "@/components/MemoryInspector";
import Navigation from "@/components/Navigation";
import { MemoryStoreProvider } from "@/hooks/useMemoryStore";

import "./globals.css";

export const metadata: Metadata = {
  title: "ZORQ — Intelligence, governed",
  description:
    "ZORQ is a control surface for a personal intelligence system: an action plane with real authorization and verification, canonical memory and governance through MEMORY//OS, and a truthful runtime — nothing simulated.",
  applicationName: "ZORQ",
  authors: [{ name: "ZORQ" }],
  openGraph: {
    title: "ZORQ",
    description: "Intelligence, governed. Memory through MEMORY//OS.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#050506",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ ["--nav-h" as string]: "3.6rem" }}>
        <a className="skip-link" href="#main">Skip to content</a>
        <MemoryStoreProvider>
          <Navigation />
          <main id="main">{children}</main>
          <MemoryInspector />
        </MemoryStoreProvider>
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}
