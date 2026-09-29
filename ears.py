"""Microphone, wake word, end-of-speech detection and speech-to-text."""
import asyncio
import collections
import json
import logging
import os
import re
import subprocess

import numpy as np
import openwakeword
import sounddevice as sd
from faster_whisper import WhisperModel
from openwakeword.model import Model
from openwakeword.vad import VAD

import config

log = logging.getLogger("ears")
RATE = 16000
FRAME = 1280            # 80 ms, the frame size openWakeWord expects
FRAME_S = FRAME / RATE

# Whisper invents these on silence or noise
JUNK = re.compile(r"^\W*(thank you|thanks for watching|you|bye|\.+|okay)?\W*$", re.I)


class Recorder:
    """Collects one utterance frame by frame. feed() returns None while still listening,
    "timeout" if nobody spoke, or the audio once they stop talking."""

    def __init__(self, vad, wait_for_speech):
        self.vad = vad
        self.wait_frames = int(wait_for_speech / FRAME_S)
        self.pre = collections.deque(maxlen=4)
        self.frames = []
        self.speaking = False
        self.silent = 0
        self.waited = 0
        vad.reset_states()

    def feed(self, chunk):
        p = self.vad.predict(chunk, frame_size=640)
        if not self.speaking:
            self.pre.append(chunk)
            self.waited += 1
            if p > 0.5:
                self.speaking = True
                self.frames.extend(self.pre)
            elif self.waited > self.wait_frames:
                return "timeout"
            return None
        self.frames.append(chunk)
        self.silent = self.silent + 1 if p < 0.3 else 0
        if self.silent * FRAME_S >= config.END_SILENCE_S or len(self.frames) * FRAME_S >= config.MAX_UTTERANCE_S:
            return np.concatenate(self.frames)
        return None


class Ears:
    def __init__(self, loop):
        self.loop = loop
        self.q = asyncio.Queue(maxsize=300)
        models_dir = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
        self.oww = Model(wakeword_models=[os.path.join(models_dir, "hey_jarvis_v0.1.onnx")],
                         inference_framework="onnx")
        self.vad = VAD()
        self.last_score = 0.0
        self.mic_name = "?"
        self.whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        self.fast = WhisperModel("base.en", device="cpu", compute_type="int8")   # rough live text while you talk
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                     device=config.MIC_DEVICE, callback=self._callback)

    def start(self):
        self.stream.start()
        self.mic_name = str(config.MIC_DEVICE) if config.MIC_DEVICE is not None else self._default_source()
        log.info("mic open: %s", self.mic_name)

    @staticmethod
    def _default_source():
        try:
            name = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True).stdout.strip()
            srcs = json.loads(subprocess.run(["pactl", "--format=json", "list", "sources"],
                                             capture_output=True, text=True).stdout)
            return next((x["description"] for x in srcs if x["name"] == name), name)
        except Exception:
            return "system default"

    def _callback(self, indata, frames, t, status):
        self.loop.call_soon_threadsafe(self._put, indata[:, 0].copy())

    def _put(self, chunk):
        if self.q.full():
            self.q.get_nowait()
        self.q.put_nowait(chunk)

    def heard_wake_word(self, chunk):
        score = max(self.oww.predict(chunk).values())
        self.last_score = score
        if score >= config.WAKE_THRESHOLD:
            log.info("wake word (%.2f)", score)
            self.reset_wake()
            return True
        return False

    def reset_wake(self):
        self.oww.reset()

    def recorder(self, wait_for_speech):
        return Recorder(self.vad, wait_for_speech)

    async def partial(self, audio):
        def run():
            segs, _ = self.fast.transcribe(audio.astype(np.float32) / 32768.0, language="en", beam_size=1,
                                           without_timestamps=True, initial_prompt=config.VOCAB)
            return " ".join(s.text for s in segs).strip()
        text = await self.loop.run_in_executor(None, run)
        return "" if JUNK.match(text) else text

    async def transcribe(self, audio):
        def run():
            segs, _ = self.whisper.transcribe(audio.astype(np.float32) / 32768.0, language="en",
                                              beam_size=1, vad_filter=True,
                                              initial_prompt=config.VOCAB)
            return " ".join(s.text for s in segs).strip()
        text = await self.loop.run_in_executor(None, run)
        return "" if JUNK.match(text) else text
