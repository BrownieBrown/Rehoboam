"use client";

import { useEffect } from "react";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  // Next.js's own error-boundary pattern: the browser console gets the real
  // error for debugging, the rendered page never does — a connection string
  // in `error.message` can reach devtools but not the page text.
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="m-6 rounded-lg border border-border bg-surface p-6">
      <h2 className="mb-1 text-base font-semibold text-text">The store is not answering</h2>
      <p className="mb-4 text-sm text-muted">
        The page could not read the database. Nothing is broken in the bot: this view is
        read-only, and the numbers reappear as soon as the store answers again.
      </p>
      <button
        onClick={reset}
        className="h-9 rounded-md border border-border-strong px-3 text-sm text-text-dim"
      >
        Try again
      </button>
    </div>
  );
}
