# Voice recognition: phase 1, your voice only

Nova can tell your voice from anyone else's: other people, a TV, or a video that happens to say "Nova".
In phase 1, only your voice can speak to Nova. Typed input from the canvas or the phone is never
checked, because it already comes from you.

## Using it

| Say | What happens |
|---|---|
| "learn my voice" | Nova shows four sentences; read them one at a time, without the wake word |
| "how's the voice check doing" | how many utterances matched, how many were dropped, the closest calls, recent drops |
| "forget my voice" | the voiceprint is deleted and any voice is accepted again |

Until you enrol, nothing is checked and the model isn't loaded.

## How it works

1. **The voiceprint.** Each utterance becomes a 256-number summary of how the voice sounds, from WeSpeaker's
   ResNet34 model (trained on VoxCeleb, ONNX, 26 MB, downloaded once to `data/models`) run by
   `onnxruntime`, which faster-whisper already installs. The features (80-band log mel filterbank)
   are computed with numpy. Nothing leaves the machine, and no recording is kept: only the numbers.
2. **The bar.** Enrolment averages your four samples and measures how alike they are. The bar is set
   0.20 below your least consistent sample, kept between 0.35 and 0.50, and worked out again each time
   from that stored consistency, so a change here applies without enrolling again. Phrases under one second
   ("stop") get 0.08 of leniency, because short clips give shakier voiceprints.
   (On the first night, a 0.15 margin capped at 0.62 turned the owner away at 0.57: samples recorded together in one
   sitting agree more than a request made later with music playing.)
3. **Only the speech.** When Whisper finds speech in less than 60% of a clip (music, a long pause),
   the voiceprint is taken from the speech segments alone. A 12-second clip with one second of
   "play the song" had been scored mostly on the music.
4. **The check.** The voiceprint is computed alongside Whisper on the same audio, so it adds no wait.
   A voice below the bar is dropped like noise. The canvas shows "(ignored: not your voice, 0.31 < 0.48)".
5. **The evidence.** Every score is logged to `data/voice_scores.jsonl` (the last 5,000). Phase 2 is
   decided from these numbers.

Measured while building it with two built-in Windows voices: same voice 0.76–0.94, different voices at most
0.37. After enrolling one of them, her new sentences scored 0.90 and 0.91 and were accepted, and the other voice
scored 0.32 and 0.33 and was rejected. A real microphone in a real room varies more, which is what the
calibrated bar is for.

## Settings

`[voice_id]` in config.toml:

- `enforce = false` scores and logs everything but drops nothing. Use it for a trial week.
- `threshold` overrides the measured bar (0 means use the measured one).
- `enabled = false` turns it off.

## Limits

- Convenience, not security: a recording of your voice can pass.
- Less reliable on very short phrases, in loud rooms, and from across the room. Check the report.
- It judges a whole utterance. Two people talking over each other aren't split apart.

## Phase 2 (once the log shows it's reliable)

Guests answered with limited permissions; other people enrolled by name; who-said-what in brainstorm recaps.

## Code

`assistant/audio/voiceid.py` (features, model, enrolment, checking, report);
`microphone.py` (`_start_voice`, `_voice_verdict`); intents `voice_enrol`, `voice_forget`, `voice_report`;
tests `tests/test_voiceid.py`.
