export type ChartPoint = { day: string; market_value: number };

export type Chart = {
  /** "M x,y L x,y …" through every point, oldest first. */
  line: string;
  /** The same path closed along the bottom, for a soft fill under the line. */
  area: string;
  /** Pixel positions and values of the extremes, for labels and dots. */
  high: { x: number; y: number; point: ChartPoint };
  low: { x: number; y: number; point: ChartPoint };
  first: ChartPoint;
  last: ChartPoint;
};

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** `day` is a plain `YYYY-MM-DD` string from the store -- parsed as UTC
 * midnight so two points on the same calendar day never drift apart from
 * local-timezone parsing. */
function parseDay(day: string): number {
  return Date.parse(`${day}T00:00:00Z`);
}

/**
 * An SVG path through the market-value series, scaled into `width` × `height`,
 * oldest point on the left. Unlike the old `sparkline`, x is spaced by the
 * *date* each point falls on, not by its index -- a gap in the series (a run
 * of days with no snapshot) shows as a gap in slope instead of being silently
 * compressed away -- and a flat series still draws (as a straight line
 * through the middle) instead of returning null: only "fewer than two
 * points" has nothing to draw. Both axes are inset by `pad`, so the first
 * and last point never sit on x=0 or x=width -- a `<circle r="4">` drawn on
 * an extreme point (the common monotone-series case clips both at once)
 * would otherwise be half outside the viewBox.
 */
export function chart(
  points: ChartPoint[],
  width: number,
  height: number,
  pad = 6,
): Chart | null {
  if (points.length < 2) return null;

  const times = points.map((p) => parseDay(p.day));
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  // Every point shares the same calendar day (or `day` failed to parse for
  // all of them) -- fall back to spreading evenly rather than dividing by 0.
  const timeSpan = maxTime - minTime || 1;

  const values = points.map((p) => p.market_value);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const flat = minV === maxV;
  const usable = Math.max(height - 2 * pad, 0);
  const usableWidth = Math.max(width - 2 * pad, 0);

  const xAt = (t: number) => round1(pad + ((t - minTime) / timeSpan) * usableWidth);
  const yAt = (v: number) =>
    flat ? round1(height / 2) : round1(pad + usable - ((v - minV) / (maxV - minV)) * usable);

  const coords = points.map((p, i) => ({ x: xAt(times[i]), y: yAt(p.market_value) }));

  const line = `M ${coords.map((c) => `${c.x},${c.y}`).join(" L ")}`;
  const lastX = coords[coords.length - 1].x;
  const firstX = coords[0].x;
  const area = `${line} L ${lastX},${height} L ${firstX},${height} Z`;

  let highIdx = 0;
  let lowIdx = 0;
  for (let i = 1; i < points.length; i++) {
    if (points[i].market_value > points[highIdx].market_value) highIdx = i;
    if (points[i].market_value < points[lowIdx].market_value) lowIdx = i;
  }

  return {
    line,
    area,
    high: { x: coords[highIdx].x, y: coords[highIdx].y, point: points[highIdx] },
    low: { x: coords[lowIdx].x, y: coords[lowIdx].y, point: points[lowIdx] },
    first: points[0],
    last: points[points.length - 1],
  };
}
