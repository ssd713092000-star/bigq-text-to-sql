import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // minimal server bundle for the Docker / Cloud Run image
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
