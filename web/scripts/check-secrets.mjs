// Fails the build if a server secret reached the client bundle.
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const NEEDLES = ["DATABASE_URL", "pooler.supabase.com", "rehoboam_bot", "ALLOWED_EMAILS"];
const ROOT = ".next/static";

async function* files(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else yield path;
  }
}

const hits = [];
for await (const path of files(ROOT)) {
  if (!/\.(js|mjs|json|css)$/.test(path)) continue;
  const text = await readFile(path, "utf8");
  for (const needle of NEEDLES) if (text.includes(needle)) hits.push(`${needle} in ${path}`);
}

if (hits.length) {
  console.error("Server secrets reached the client bundle:\n" + hits.join("\n"));
  process.exit(1);
}
console.log(`check-secrets: clean (${NEEDLES.length} patterns)`);
