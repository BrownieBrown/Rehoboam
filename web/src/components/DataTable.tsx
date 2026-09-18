import Link from "next/link";

export type Column<T> = {
  key: string;
  label: string;
  align?: "left" | "right";
  sortable?: boolean;
  /** Shown as the header's tooltip. */
  hint?: string;
  cell: (row: T) => React.ReactNode;
};

/**
 * The one table every page uses. Sorting is a link to `?sort=&dir=`, not
 * client state — the page re-renders on the server with the new order, so
 * this component never ships JavaScript to the browser.
 */
export function DataTable<T>({
  columns,
  rows,
  sort,
  dir,
  basePath,
  query,
  rowClass,
}: {
  columns: Column<T>[];
  rows: T[];
  sort: string;
  dir: "asc" | "desc";
  basePath: string;
  query?: Record<string, string | undefined>;
  /** Extra classes for one row, e.g. dimming a bench player. */
  rowClass?: (row: T) => string;
}) {
  function href(key: string) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query ?? {})) if (v) params.set(k, v);
    params.set("sort", key);
    params.set("dir", key === sort && dir === "desc" ? "asc" : "desc");
    return `${basePath}?${params.toString()}`;
  }

  return (
    // `overflow-x-auto`, never `overflow-hidden`: a table wider than the
    // screen must scroll sideways, not lose its right-hand columns.
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                title={c.hint}
                className={`h-10 whitespace-nowrap border-b border-border-strong px-3 text-[11px] font-semibold uppercase tracking-[0.08em] ${
                  c.align === "left" ? "text-left" : "text-right"
                } ${c.key === sort ? "text-accent" : "text-muted"}`}
              >
                {c.sortable === false ? (
                  c.label
                ) : (
                  <Link href={href(c.key)} className="hover:text-text">
                    {c.label}
                    {c.key === sort ? (dir === "desc" ? " ▾" : " ▴") : ""}
                  </Link>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={rowClass?.(row)}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`tnum h-11 whitespace-nowrap border-b border-border px-3 ${
                    c.align === "left" ? "text-left" : "text-right"
                  }`}
                >
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="h-24 text-center text-sm text-muted">
                Nothing here yet.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
