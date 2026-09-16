const PUBLIC = ["/login", "/auth/callback"];

/** Shared by the middleware and its test; `path` may carry a query string. */
export function isPublicPath(path: string): boolean {
  const clean = path.split("?")[0];
  return PUBLIC.some((p) => clean === p || clean.startsWith(p + "/"));
}
