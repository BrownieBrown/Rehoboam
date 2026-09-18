export type Point = { day: string; market_value: number };

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/**
 * An SVG polyline path through the values, scaled into `width` × `height`,
 * oldest on the left. Null when there is nothing to draw (fewer than two
 * points, or every value identical — a flat line says nothing a number
 * doesn't). Also returns the first and last value so the caller can label
 * the ends without recomputing them.
 */
export function sparkline(
  points: Point[],
  width: number,
  height: number,
): { path: string; first: Point; last: Point } | null {
  if (points.length < 2) return null;
  const values = points.map((p) => p.market_value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return null;
  const n = points.length;
  const path = points
    .map((p, i) => {
      const x = round1((i / (n - 1)) * width);
      // Highest value at the top (y = 0), lowest at the bottom (y = height) — SVG's
      // y axis grows downward, so the scale is inverted from the raw value.
      const y = round1(height - ((p.market_value - min) / (max - min)) * height);
      return `${x},${y}`;
    })
    .join(" ");
  return { path, first: points[0], last: points[n - 1] };
}
