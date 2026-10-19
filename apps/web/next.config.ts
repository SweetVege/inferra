import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api is proxied to the FastAPI service so session
// cookies stay first-party.
const apiUrl = process.env.INFERRA_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  experimental: {
    // The proxy cuts request bodies at 10 MB by default. Uploads normally go straight to object
    // storage, but local development (no bucket) sends files through this proxy.
    middlewareClientMaxBodySize: "50mb",
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
