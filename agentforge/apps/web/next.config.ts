import path from "node:path";
import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to the FastAPI service, so the
// session cookie is first-party and SameSite=Lax applies (architecture §5.3).
// Rewrites are fixed at build time: the Docker image is built with AGENTFORGE_API_ORIGIN=http://api:8000.
const apiOrigin = process.env.AGENTFORGE_API_ORIGIN ?? "http://127.0.0.1:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const config: NextConfig = {
  output: "standalone",
  // Monorepo root, so standalone output traces workspace dependencies correctly.
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
