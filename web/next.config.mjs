/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The Postgres driver must not be bundled into the edge/client graph.
  serverExternalPackages: ["postgres"],
};

export default nextConfig;
