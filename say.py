"""Ask Fish Audio to say something, and play it as it arrives.

    python say.py
    python say.py "whatever you want it to say"
    python say.py --voice <reference_id> "in a particular voice"
    python say.py --keep out.wav "also save it"

Streams raw PCM straight to the sound card, so sound starts about 0.65 seconds in
instead of after the whole clip is synthesised, and nothing is written to disk unless
you ask for it.

It streams for a second reason too. The API's WAV output carries a streaming header
that declares its length as 4,294,967,040 bytes -- a 4 GB clip -- and Windows'
PlaySound reads that number, refuses, and plays nothing at all. That is silent: the
request succeeds, the bytes are fine, and you hear nothing. Raw PCM has no header to
be wrong, so this cannot happen.

Delivery tags go inline and the model acts on them:

    [chuckle] [emphasis] [long pause] [sigh] [whisper] [laugh]
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.fish.audio/v1/tts"
MODEL = "s2.1-pro-free"
KEY_NAME = "FISH_AUDIO_S2.1_PRO"
# What the API sends for format="pcm": signed 16-bit, one channel. Confirmed by timing
# a known clip against its byte count.
RATE, CHANNELS, WIDTH = 44100, 1, 2
CHUNK = 4096
DEFAULT = ("[chuckle] Right, that's the layout done. [long pause] "
           "Now I'm ideating the page structure.")


def find_key() -> str:
    """The key, from the environment or from the .env beside this file."""
    found = os.environ.get(KEY_NAME, "").strip()
    if found:
        return found
    try:
        lines = (Path(__file__).resolve().parent / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        name, _, value = line.strip().partition("=")
        if name.strip() == KEY_NAME:
            return value.strip().strip("'\"")
    return ""


def open_speaker():
    """A sound card to write PCM into. None means play nothing and say so."""
    try:
        import sounddevice

        stream = sounddevice.RawOutputStream(samplerate=RATE, channels=CHANNELS, dtype="int16")
        stream.start()
        return stream, stream.write, stream
    except Exception:
        pass
    try:
        import pyaudio

        audio = pyaudio.PyAudio()
        stream = audio.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, output=True)
        return stream, stream.write, audio
    except Exception as error:
        print(f"no sound output available ({type(error).__name__}: {error})")
        return None, None, None


def wav_header(data_bytes: int) -> bytes:
    """A correct RIFF header, since the one the API sends is not.

    Only used by --keep. The sizes here are the real ones, so the file plays in
    anything -- unlike the API's own WAV, which no Windows player will touch.
    """
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE,
                          RATE * CHANNELS * WIDTH, CHANNELS * WIDTH, WIDTH * 8)
            + b"data" + struct.pack("<I", data_bytes))


def say(text: str, key: str, voice: str = "", keep: Path | None = None) -> int:
    payload = {"text": text, "format": "pcm"}
    if voice:
        payload["reference_id"] = voice
    request = urllib.request.Request(
        API, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "model": MODEL})

    started = time.time()
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        print(f"the API refused it: HTTP {error.code} -- "
              f"{error.read().decode('utf-8', 'replace')[:300]}")
        return 1
    except Exception as error:
        print(f"could not reach the API: {type(error).__name__} -- {error}")
        return 1

    stream, write, owner = open_speaker()
    collected = bytearray() if keep else None
    leftover = b""
    total = 0
    first_sound = None

    with response:
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if collected is not None:
                collected += chunk
            if write is None:
                continue
            # A frame is two bytes, and an HTTP chunk does not have to end on one, so
            # any odd trailing byte waits for the next chunk rather than being played
            # as half a sample.
            block = leftover + chunk
            usable = len(block) - (len(block) % (WIDTH * CHANNELS))
            leftover = block[usable:]
            if usable:
                if first_sound is None:
                    first_sound = time.time() - started
                    print(f"sound starts after {first_sound:.2f}s")
                write(block[:usable])

    if write is not None and leftover:
        write(leftover + b"\x00" * (WIDTH * CHANNELS - len(leftover)))

    seconds = total / float(RATE * CHANNELS * WIDTH)
    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception:
            try:
                stream.stop_stream()
                stream.close()
                owner.terminate()
            except Exception:
                pass

    print(f"{total // 1024} KB, {seconds:.1f}s of audio, {time.time() - started:.2f}s in total")
    if keep is not None and collected is not None:
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_bytes(wav_header(len(collected)) + bytes(collected))
        print(f"saved to {keep}")
    if write is None:
        print("nothing was played: no working sound output was found")
        return 1
    return 0


def main(argv: list[str]) -> int:
    voice, keep, words = "", None, []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--voice" and index + 1 < len(argv):
            voice, index = argv[index + 1], index + 2
        elif argument == "--keep" and index + 1 < len(argv):
            keep, index = Path(argv[index + 1]), index + 2
        elif argument in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            words.append(argument)
            index += 1

    key = find_key()
    if not key:
        print(f"No key. Put {KEY_NAME}=... in .env beside this file, or set it in the environment.")
        return 1

    text = " ".join(words) or DEFAULT
    print(f"saying: {text}")
    return say(text, key, voice, keep)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
