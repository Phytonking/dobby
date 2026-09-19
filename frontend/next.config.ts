import type { NextConfig } from 'next'

// Server-side rewrite target: inside Docker, use the service name.
// INTERNAL_API_URL is set to http://dashboard:8000 in compose.yaml.
const INTERNAL_API = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

const nextConfig: NextConfig = {
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${INTERNAL_API}/:path*`,
      },
    ]
  },
}

export default nextConfig
