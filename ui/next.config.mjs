/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000",
  },
  experimental: {
    // Tree-shake barrel imports from these heavy packages so we only ship the
    // icons/components actually referenced at the call sites.
    optimizePackageImports: [
      "lucide-react",
      "recharts",
      "framer-motion",
      "@xyflow/react",
      "@radix-ui/react-slot",
    ],
  },
  compiler: {
    // Strip console.* calls in production builds (keep error/warn for ops).
    removeConsole:
      process.env.NODE_ENV === "production" ? { exclude: ["error", "warn"] } : false,
  },
};

export default nextConfig;
