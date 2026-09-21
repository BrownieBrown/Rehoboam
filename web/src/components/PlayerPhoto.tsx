/**
 * A player's photo and his club's crest -- `image_path`/`crest_path` are
 * object paths inside the public `kickbase` Supabase Storage bucket
 * (migration 021), and they are null for every row today: the sync job that
 * fills them cannot run until the owner adds a storage key (see
 * `enrichment/images.py`). So the fallback below is not an edge case --
 * it is what the dashboard actually shows right now -- and both components
 * must degrade to it without ever leaving a broken-image icon or a hole in
 * the layout.
 *
 * Plain `<img>`, not `next/image`: a ~16 KB avatar or crest doesn't justify
 * running Vercel's image optimizer, and this app never runs `next build`'s
 * optimizer pipeline for anything else either.
 */

/**
 * Two letters off a name, for the fallback circle: one word keeps its own
 * first two letters, several words take the first letter of the first and
 * the last -- same rule the player panel used before this component existed.
 */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * The public Storage URL for an object path in the `kickbase` bucket, or
 * `null` when there is nothing to show. Null and "" both count as "no
 * path" -- a hand-edited or partially-synced row could carry either -- and
 * a missing `NEXT_PUBLIC_SUPABASE_URL` falls back the same way rather than
 * throwing, the same "a hand-edited URL must never break the page" rule
 * every other part of this app follows. Reads the env var directly, the
 * same one `src/lib/supabase.ts`'s `env()` already reads for the same
 * project -- no new variable.
 */
export function storageUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  const base = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!base) return null;
  return `${base}/storage/v1/object/public/kickbase/${path}`;
}

const PHOTO_BOX: Record<number, string> = {
  30: "h-[30px] w-[30px]",
  34: "h-[34px] w-[34px]",
  66: "h-[66px] w-[66px]",
};

const PHOTO_TEXT: Record<number, string> = {
  30: "text-[10px]",
  34: "text-[11px]",
  66: "text-lg",
};

/**
 * A player's photo at `size` px, or a circle of his initials on the app's
 * surface colour when there is none.
 */
export function PlayerPhoto({
  path,
  name,
  size,
}: {
  path: string | null | undefined;
  name: string;
  /** One of the three sizes the pixel references use: 30 px in market table
   * rows, 34 px in the players list, 66 px in the player panel header. */
  size: 30 | 34 | 66;
}) {
  const src = storageUrl(path);
  const box = PHOTO_BOX[size] ?? PHOTO_BOX[34];
  if (!src) {
    return (
      <div
        aria-hidden="true"
        className={`flex ${box} shrink-0 items-center justify-center rounded-full border border-border bg-bg font-semibold text-muted ${PHOTO_TEXT[size] ?? PHOTO_TEXT[34]}`}
      >
        {initials(name)}
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- see the module doc comment above.
    <img
      src={src}
      alt=""
      className={`${box} shrink-0 rounded-full border border-border bg-bg object-cover`}
    />
  );
}

const CREST_BOX: Record<number, string> = {
  13: "h-[13px] w-[13px]",
  14: "h-[14px] w-[14px]",
  16: "h-[16px] w-[16px]",
};

/**
 * A club crest at `size` px, or nothing at all. Unlike `PlayerPhoto`, a
 * missing crest is never a placeholder -- it renders as an absence beside
 * the club name, not a gap-filling circle, since there's no per-club
 * initial worth drawing.
 */
export function ClubCrest({
  path,
  size,
}: {
  path: string | null | undefined;
  /** 13 px beside a market row's club line, 14 px in the players list,
   * 16 px in the player panel header -- the pixel references' own range. */
  size: 13 | 14 | 16;
}) {
  const src = storageUrl(path);
  if (!src) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- see PlayerPhoto.
    <img src={src} alt="" className={`${CREST_BOX[size] ?? CREST_BOX[14]} shrink-0 object-contain`} />
  );
}
