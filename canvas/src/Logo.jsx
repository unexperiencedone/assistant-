import { LOCKUP, MARK, WORDMARK } from "./brand.js";

// The owner's logo, traced into one path each (packaging/make_brand.py). It fills with
// currentColor, so the surface decides: ink on paper in the canvas, paper on ink in the
// overlay. Height is set by the caller; width follows the shape.
const SHAPES = { lockup: LOCKUP, wordmark: WORDMARK, mark: MARK };

export default function Logo({ kind = "wordmark", height = 32, label = "Nova", className = "" }) {
  const shape = SHAPES[kind];
  const [, , w, h] = shape.viewBox.split(" ").map(Number);
  return (
    <svg className={className} viewBox={shape.viewBox} width={(height * w) / h} height={height} role="img" aria-label={label}>
      <path fill="currentColor" fillRule="evenodd" d={shape.d} />
    </svg>
  );
}
