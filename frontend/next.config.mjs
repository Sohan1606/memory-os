/** @type {import('next').NextConfig} */
// NOTE: rewrites are evaluated at BUILD time and compiled into the build
// output. If you change the backend port you must rebuild - exporting
// BACKEND_URL before `next start` alone has no effect.
const API = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    // A local CPU model can take 30s+ for a multi-step tool-calling turn.
    // Next's default rewrite proxy timeout is 30s, which surfaced as an
    // intermittent 500 (socket hang up) on /api/chat during browser QA while
    // curl to the same backend succeeded.
    proxyTimeout: 300_000,
  },
  // The browser always calls same-origin /api/* ; Next proxies to FastAPI.
  // This keeps the app working behind any host/proxy without CORS surprises.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
