import { requireSession } from "@/lib/auth";
import { shellFacts } from "@/lib/queries";
import { Sidebar } from "@/components/Sidebar";

/**
 * The shell of the four data pages. It reads the store (budget, next kickoff,
 * lineup state, player count), so it lives inside the `(app)` route group:
 * `/login` and `/auth/callback` sit outside it and never render it. It asserts
 * the session itself before that read, like every page does, rather than rely
 * on the middleware alone.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  const facts = await shellFacts();
  return (
    <div className="flex min-h-screen">
      <Sidebar facts={facts} />
      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
