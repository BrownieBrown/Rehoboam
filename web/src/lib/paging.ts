/** Rows per page on the Players table. */
export const PAGE_SIZE = 50;

/**
 * The highest page a URL may ask for before the count is known. 1,000 pages
 * of 50 is 50,000 rows, far past any player universe; the real ceiling is
 * applied afterwards by `clampPage`.
 */
export const MAX_PAGE = 1000;

/**
 * A `?page=` value as a page number. Only plain ASCII digits with no leading
 * zero are a page; anything else (absent, empty, a sign, whitespace, a
 * decimal, an exponent, hex, non-ASCII digits, a repeated parameter) is page 1.
 * A page past `MAX_PAGE` is `MAX_PAGE`. The result is an integer in
 * [1, MAX_PAGE], so the offset built from it is always a bounded number.
 */
export function parsePage(raw: unknown): number {
  if (typeof raw !== "string" || !/^[1-9][0-9]*$/.test(raw)) return 1;
  const page = Number(raw);
  return Number.isSafeInteger(page) ? Math.min(page, MAX_PAGE) : MAX_PAGE;
}

/** The last page that has rows (page 1 when there are none), and `page` held inside it. */
export function clampPage(page: number, total: number): number {
  const last = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return Math.min(Math.max(1, page), last);
}

/** The rows a page skips. Passed to SQL as a bound parameter, never interpolated. */
export function pageOffset(page: number): number {
  return (page - 1) * PAGE_SIZE;
}

/** "1–50 of 213": the rows actually shown, out of every row the filters match. */
export function pageSummary(offset: number, shown: number, total: number): string {
  if (shown === 0) return `0 of ${total}`;
  return `${offset + 1}–${offset + shown} of ${total}`;
}

/**
 * A link to `page` that keeps every other non-empty parameter as it is.
 * Page 1 carries no `page` parameter at all.
 */
export function pageHref(
  basePath: string,
  params: Record<string, string | undefined>,
  page: number,
): string {
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (k !== "page" && typeof v === "string" && v !== "") next.set(k, v);
  }
  if (page > 1) next.set("page", String(page));
  const qs = next.toString();
  return qs ? `${basePath}?${qs}` : basePath;
}
