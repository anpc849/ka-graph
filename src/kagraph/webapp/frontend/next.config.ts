import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: [
    "*.loca.lt",
    "*.loca.run",
    "*.ngrok-free.app",
    "*.trycloudflare.com",
    "localhost",
    "127.0.0.1",
  ],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*", // Proxy to FastAPI backend
      },
    ];
  },
};

export default nextConfig;
