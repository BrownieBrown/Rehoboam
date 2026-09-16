import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { shellFacts } from "@/lib/queries";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"] });

export const metadata: Metadata = { title: "Rehoboam", description: "The bot's own dashboard" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const facts = await shellFacts();
  return (
    <html lang="en">
      <body className={`${plex.className} flex min-h-screen bg-bg text-text`}>
        <Sidebar facts={facts} />
        <main className="flex min-w-0 flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
