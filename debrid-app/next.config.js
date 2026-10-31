/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: { unoptimized: true },
  outputFileTracingIncludes: {
    "/*": ["./data/titles.sqlite"],
    "/[...p]": ["./data/titles.sqlite"],
  },
  experimental: {
    outputFileTracingIncludes: {
      "/*": ["./data/titles.sqlite"],
      "/[...p]": ["./data/titles.sqlite"],
    },
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" },
          { key: "Access-Control-Allow-Methods", value: "GET, POST, OPTIONS" },
          { key: "Access-Control-Allow-Headers", value: "Content-Type, Authorization" },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
