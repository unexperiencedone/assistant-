import { useCallback, useEffect, useRef, useState } from "react";
import { canListen, canSpeak, speechRecognition } from "./device.js";

/**
 * Talking to Nova from the phone.
 *
 * Nova's own ears are the PC's microphone, on the PC. A phone across the house cannot
 * use those, so this is the phone's own: the browser listens, and what it hears is sent
 * as an ordinary request marked as coming from the phone. That is also why the
 * permission prompt finally appears — it is this browser asking, not Nova.
 *
 * Replies marked `origin: "phone"` are read out here for the same reason: the PC
 * deliberately stays quiet for them (assistant/controller.py `say`).
 */

/** Reads out assistant replies that were meant for this phone, once each. */
export function usePhoneSpeech(conversation, enabled) {
  const spokenUpTo = useRef(null);

  useEffect(() => {
    if (!enabled || !canSpeak()) return;
    const rows = conversation ?? [];
    if (!rows.length) return;
    // First run: remember where we came in, so opening the app does not recite history.
    if (spokenUpTo.current === null) {
      spokenUpTo.current = rows.length;
      return;
    }
    const fresh = rows.slice(spokenUpTo.current);
    spokenUpTo.current = rows.length;
    for (const row of fresh) {
      if (row.role !== "assistant" || row.origin !== "phone" || !row.text) continue;
      try {
        const utterance = new SpeechSynthesisUtterance(row.text);
        utterance.rate = 1.05;
        window.speechSynthesis.speak(utterance);
      } catch {
        /* no voice available: the reply is on screen either way */
      }
    }
  }, [conversation, enabled]);
}

/**
 * Hold-to-talk. One tap starts listening, the next stops; whatever was heard is sent
 * when recognition ends, so a dropped word does not send half a request.
 */
export default function PhoneVoice({ say, onInterim }) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recognition = useRef(null);
  const heard = useRef("");

  const stop = useCallback(() => {
    recognition.current?.stop();
  }, []);

  const start = useCallback(() => {
    if (listening) {
      stop();
      return;
    }
    const engine = speechRecognition();
    if (!engine) {
      setError("This browser cannot listen.");
      return;
    }
    heard.current = "";
    engine.lang = navigator.language || "en-US";
    engine.interimResults = true;
    engine.continuous = false;

    engine.onresult = (event) => {
      let text = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) text += event.results[i][0].transcript;
      heard.current = text.trim();
      onInterim?.(heard.current);
    };
    engine.onerror = (event) => {
      // "not-allowed" is the permission prompt being declined: say so plainly.
      setError(event.error === "not-allowed" ? "Microphone permission was denied." : `Couldn't listen: ${event.error}`);
    };
    engine.onend = () => {
      setListening(false);
      recognition.current = null;
      const text = heard.current.trim();
      onInterim?.("");
      if (text) say(text);
    };

    try {
      engine.start();
      recognition.current = engine;
      setListening(true);
      setError("");
    } catch {
      setError("Couldn't start listening.");
    }
  }, [listening, onInterim, say, stop]);

  useEffect(() => () => recognition.current?.abort?.(), []);

  if (!canListen()) return null;

  return (
    <button
      type="button"
      className={`mic-btn${listening ? " on" : ""}`}
      onClick={start}
      aria-pressed={listening}
      aria-label={listening ? "Stop listening" : "Speak to Nova"}
      title={error || (listening ? "Listening — tap to send" : "Speak to Nova")}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      </svg>
    </button>
  );
}
