import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Draggable edges for the trays.
 *
 * Widths are kept per panel in localStorage, so the canvas comes back the shape you
 * left it. Double-clicking an edge puts that panel back to its default. Below the
 * narrow breakpoint the stylesheet stacks everything, and the saved widths are simply
 * not applied.
 */

const NARROW = 1100;

export function useIsNarrow() {
  const [narrow, setNarrow] = useState(() => window.innerWidth < NARROW);
  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${NARROW - 1}px)`);
    const update = (event) => setNarrow(event.matches);
    setNarrow(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return narrow;
}

/** One remembered width, with its limits. */
export function useWidth(key, initial, min, max) {
  const [width, setWidth] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(`nova-width-${key}`));
      return saved >= min && saved <= max ? saved : initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (value) => {
      const clamped = Math.round(Math.min(max, Math.max(min, value)));
      setWidth(clamped);
      try {
        localStorage.setItem(`nova-width-${key}`, String(clamped));
      } catch {
        /* private window: it just won't be remembered */
      }
    },
    [key, min, max],
  );
  const reset = useCallback(() => {
    setWidth(initial);
    try {
      localStorage.removeItem(`nova-width-${key}`);
    } catch {
      /* nothing to forget */
    }
  }, [key, initial]);
  return [width, set, reset];
}

/**
 * The edge itself. `side` says which way the panel grows: "right" for a panel on the
 * left of the handle, "left" for one on the right of it.
 */
export function ResizeHandle({ side = "right", width, onResize, onReset, label }) {
  const start = useRef(0);

  const down = (event) => {
    event.preventDefault();
    start.current = event.clientX;
    const startWidth = width;
    const onMove = (moveEvent) => {
      const delta = moveEvent.clientX - start.current;
      onResize(startWidth + (side === "right" ? delta : -delta));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.classList.remove("resizing");
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // Keyboard: the same panel, 16px at a time, for anyone not using a mouse.
  const key = (event) => {
    if (event.key === "ArrowLeft") onResize(width + (side === "right" ? -16 : 16));
    else if (event.key === "ArrowRight") onResize(width + (side === "right" ? 16 : -16));
    else if (event.key === "Home") onReset?.();
    else return;
    event.preventDefault();
  };

  return (
    <div
      className={`resize-handle resize-${side}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onPointerDown={down}
      onDoubleClick={onReset}
      onKeyDown={key}
    />
  );
}
