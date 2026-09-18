import { describe, expect, it } from "vitest";
import { ClubCrest, initials, PlayerPhoto, storageUrl } from "./PlayerPhoto";

describe("initials", () => {
  it("keeps a single word's own first two letters", () => {
    expect(initials("Neymar")).toBe("NE");
  });
  it("takes the first letter of the first and last word for several", () => {
    expect(initials("Igor Matanović")).toBe("IM");
    expect(initials("Jeremiaha van den Berg")).toBe("JB");
  });
  it("uppercases regardless of the name's own casing", () => {
    expect(initials("willi orban")).toBe("WO");
  });
  it("falls back to a question mark for nothing at all", () => {
    expect(initials("")).toBe("?");
    expect(initials("   ")).toBe("?");
  });
});

describe("storageUrl", () => {
  it("builds the public Supabase Storage URL for a path", () => {
    const prev = process.env.NEXT_PUBLIC_SUPABASE_URL;
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://qznixprbyldatdjzorbq.supabase.co";
    expect(storageUrl("players/a.png")).toBe(
      "https://qznixprbyldatdjzorbq.supabase.co/storage/v1/object/public/kickbase/players/a.png",
    );
    process.env.NEXT_PUBLIC_SUPABASE_URL = prev;
  });
  it("is null for a null path", () => {
    expect(storageUrl(null)).toBeNull();
  });
  it("is null for an empty-string path", () => {
    expect(storageUrl("")).toBeNull();
  });
  it("is null for undefined", () => {
    expect(storageUrl(undefined)).toBeNull();
  });
});

describe("PlayerPhoto", () => {
  it("renders the initials fallback, not an <img>, for a null path", () => {
    const el = PlayerPhoto({ path: null, name: "Igor Matanović", size: 34 });
    expect(el.type).toBe("div");
    expect(el.props.children).toBe("IM");
  });

  it("renders the initials fallback for an empty-string path", () => {
    const el = PlayerPhoto({ path: "", name: "Neymar", size: 34 });
    expect(el.type).toBe("div");
    expect(el.props.children).toBe("NE");
  });

  it("renders an <img> when there is a real path", () => {
    const prev = process.env.NEXT_PUBLIC_SUPABASE_URL;
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    const el = PlayerPhoto({ path: "players/a.png", name: "Igor Matanović", size: 66 });
    expect(el.type).toBe("img");
    expect(el.props.src).toBe("https://example.supabase.co/storage/v1/object/public/kickbase/players/a.png");
    process.env.NEXT_PUBLIC_SUPABASE_URL = prev;
  });
});

describe("ClubCrest", () => {
  it("renders nothing -- not a placeholder -- for a null path", () => {
    expect(ClubCrest({ path: null, size: 14 })).toBeNull();
  });

  it("renders nothing for an empty-string path", () => {
    expect(ClubCrest({ path: "", size: 14 })).toBeNull();
  });

  it("renders an <img> when there is a real path", () => {
    const prev = process.env.NEXT_PUBLIC_SUPABASE_URL;
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    const el = ClubCrest({ path: "teams/7.png", size: 16 });
    expect(el?.type).toBe("img");
    expect(el?.props.src).toBe("https://example.supabase.co/storage/v1/object/public/kickbase/teams/7.png");
    process.env.NEXT_PUBLIC_SUPABASE_URL = prev;
  });
});
