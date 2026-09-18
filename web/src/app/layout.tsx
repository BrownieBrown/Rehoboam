import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"] });

export const metadata: Metadata = { title: "Rehoboam", description: "The bot's own dashboard" };

/**
 * Font, styles and the document only. The sidebar and its store read live in
 * `(app)/layout.tsx`, so `/login` and `/auth/callback` render without them.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${plex.className} min-h-screen bg-bg text-text`}>{children}</body>
    </html>
  );
}
