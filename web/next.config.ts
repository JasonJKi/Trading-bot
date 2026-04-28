import type { NextConfig } from "next";

const API_TARGET = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Proxy /api/* to the FastAPI backend so the frontend can use a same-origin
  // path in production *and* development. Avoids CORS in dev too.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_TARGET}/api/:path*` },
    ];
  },
};

export default nextConfig;
