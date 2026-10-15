import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api is proxied to the FastAPI service so session
// cookies stay first-party.
const apiUrl = process.env.INFERRA_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
