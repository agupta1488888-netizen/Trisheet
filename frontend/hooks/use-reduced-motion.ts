"use client";

/**
 * Mirrors `globals.css`'s blanket `prefers-reduced-motion` rule for
 * animation CSS cannot reach. That rule zeroes out transition and animation
 * durations for everyone; a three.js render loop runs its own
 * `requestAnimationFrame` loop in JavaScript, so anything driven by it needs
 * this same check instead.
 */

import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia(QUERY);
    setReduced(mediaQuery.matches);

    const onChange = (event: MediaQueryListEvent) => {
      setReduced(event.matches);
    };

    mediaQuery.addEventListener("change", onChange);
    return () => {
      mediaQuery.removeEventListener("change", onChange);
    };
  }, []);

  return reduced;
}
