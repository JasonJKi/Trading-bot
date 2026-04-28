import type { NextConfig } from "next";

const API_TARGET = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const isExport = process.env.NEXT_BUILD_MODE === "export";

const nextConfig: NextConfig = {
  // In dev: standard server build, /api/* rewrites proxy to FastAPI.
  // In prod: static export — FastAPI serves the built files directly, so no
  // Node runtime is needed. The dashboard talks to /api/* on the same origin.
  ...(isExport
    ? { output: "export", trailingSlash: true, images: { unoptimized: true } }
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${API_TARGET}/api/:path*` }];
        },
      }),
};

export default nextConfig;
