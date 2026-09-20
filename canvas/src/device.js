import { useEffect, useState } from "react";

/**
 * Which machine this canvas is running on, and how big it is.
 *
 * The server can tell a remote request from a local one by its address, but only the
 * browser knows whether it is a phone in your hand or a laptop on the tailnet — so the
 * client says which it is, and the address is the server's fallback.
 */

const PHONE = "(pointer: coarse) and (max-width: 820px)";   // really in a hand
const NARROW = "(max-width: 700px)";                        // laid out like a phone

export function isPhone() {
  return window.matchMedia?.(PHONE).matches ?? false;
}

/** "desktop" | "phone" — sent with every request so the reply knows where to go back to. */
export function clientOrigin() {
  return isPhone() ? "phone" : "desktop";
}

export const isNarrow = () => window.matchMedia?.(NARROW).matches ?? false;

/** Re-renders when the window crosses a threshold (rotating, resizing). */
function useMedia(media, initial) {
  const [matches, setMatches] = useState(initial);
  useEffect(() => {
    const query = window.matchMedia(media);
    const update = (event) => setMatches(event.matches);
    setMatches(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [media]);
  return matches;
}

/** True on a real phone: what decides whether this canvas speaks and listens itself. */
export const usePhone = () => useMedia(PHONE, isPhone());

/** True whenever the window is phone-shaped, touch or not: what decides the layout. */
export const useNarrow = () => useMedia(NARROW, isNarrow());

/** The browser's own speech recognition, where there is one (Chrome on Android). */
export function speechRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  return Recognition ? new Recognition() : null;
}

export const canListen = () => Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
export const canSpeak = () => typeof window.speechSynthesis !== "undefined";
