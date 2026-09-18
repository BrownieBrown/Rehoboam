import { describe, expect, it } from "vitest";
import { ago, countdown, DASH, money, num, pct, signed, signedPct, signedMoney } from "./format";

describe("money", () => {
  it("writes the exact figure with separators", () => {
    expect(money(65089670)).toBe("65,089,670");
    expect(money(500000)).toBe("500,000");
    expect(money(0)).toBe("0");
  });
  it("never abbreviates", () => {
    expect(money(65089670)).not.toMatch(/M|m|k/);
  });
  it("shows an em dash for nothing", () => {
    expect(money(null)).toBe("—");
  });
  it("uses the same minus sign as signed/signedPct on a negative value, not a hyphen", () => {
    expect(money(-500000).startsWith("−")).toBe(true);
    expect(money(-500000).charCodeAt(0)).toBe("−".charCodeAt(0));
  });
  it("keeps exact separators on a negative value", () => {
    expect(money(-500000)).toBe("−500,000");
  });
  it("shows zero with no sign", () => {
    expect(money(0)).toBe("0");
  });
  it("leaves a positive value unchanged", () => {
    expect(money(65089670)).toBe("65,089,670");
  });
});

describe("signedPct", () => {
  it("marks a rise positive and a fall negative", () => {
    expect(signedPct(2)).toEqual({ text: "+2.00%", tone: "positive" });
    expect(signedPct(-0.77)).toEqual({ text: "−0.77%", tone: "negative" });
  });
  it("treats exactly zero as neutral", () => {
    expect(signedPct(0)).toEqual({ text: "0.00%", tone: "neutral" });
  });
  it("uses a real minus sign, not a hyphen", () => {
    expect(signedPct(-1).text.startsWith("−")).toBe(true);
  });
  it("shows an em dash for nothing", () => {
    expect(signedPct(null)).toEqual({ text: "—", tone: "neutral" });
  });
});

describe("signed", () => {
  it("rounds to the digits asked for", () => {
    expect(signed(-109.04, 1)).toEqual({ text: "−109.0", tone: "negative" });
    expect(signed(67.44, 1)).toEqual({ text: "+67.4", tone: "positive" });
  });
});

describe("num", () => {
  it("formats plain numbers and blanks null", () => {
    expect(num(202, 1)).toBe("202.0");
    expect(num(null)).toBe("—");
  });

  it("uses the same minus sign as money/signed/signedPct on a negative value, not a hyphen", () => {
    expect(num(-12, 0)).toBe("−12");
    expect(num(-12).charCodeAt(0)).toBe("−".charCodeAt(0));
    expect(num(-3.5, 1)).toBe("−3.5");
  });
});

describe("countdown", () => {
  const now = 1_000_000;
  it("counts hours down to one decimal", () => {
    expect(countdown(now + 88200, now)).toBe("24.5 h");
  });
  it("switches to minutes under an hour", () => {
    expect(countdown(now + 2880, now)).toBe("48 min");
  });
  it("says expired in the past and blanks a manager listing", () => {
    expect(countdown(now - 1, now)).toBe("expired");
    expect(countdown(null, now)).toBe("—");
  });
});

describe("ago", () => {
  it("names the UTC clock time and the distance", () => {
    const t = Date.UTC(2026, 8, 16, 8, 1, 13) / 1000;
    expect(ago(t, t + 3 * 3600)).toBe("08:01 UTC · 3 h ago");
  });
});

describe("pct", () => {
  it("rounds a 0-1 probability to a whole percent", () => {
    expect(pct(0.8365)).toBe("84%");
  });
  it("handles the boundaries", () => {
    expect(pct(0)).toBe("0%");
    expect(pct(1)).toBe("100%");
  });
  it("shows an em dash for nothing", () => {
    expect(pct(null)).toBe(DASH);
    expect(pct(undefined)).toBe(DASH);
  });
});

describe("signedMoney", () => {
  it("formats negative values with grouping and a real minus sign", () => {
    expect(signedMoney(-8094199)).toEqual({ text: "−8,094,199", tone: "negative" });
    expect(signedMoney(-8094199).text.charCodeAt(0)).toBe("−".charCodeAt(0));
  });
  it("formats positive values with grouping and a plus sign", () => {
    expect(signedMoney(2000000)).toEqual({ text: "+2,000,000", tone: "positive" });
  });
  it("formats zero without a sign", () => {
    expect(signedMoney(0)).toEqual({ text: "0", tone: "neutral" });
  });
  it("shows an em dash for null and undefined", () => {
    expect(signedMoney(null)).toEqual({ text: "—", tone: "neutral" });
    expect(signedMoney(undefined)).toEqual({ text: "—", tone: "neutral" });
  });
  it("never abbreviates, even for very large values", () => {
    const result = signedMoney(-1234567890);
    expect(result.text).not.toMatch(/M|m|k|B|b/);
    expect(result.text).toBe("−1,234,567,890");
  });
});
