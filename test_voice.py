"""Run: .venv/bin/python test_voice.py ["text"]  -> logs/voice-kokoro.wav (and logs/voice-piper.wav if PIPER_MODEL is set), rendered by Mouth._synth."""
import os, sys, wave
import numpy as np
import config, mouth

text = sys.argv[1] if len(sys.argv) > 1 else "Good evening, sir. Shall I render, utilising proposed specifications?"
os.makedirs("logs", exist_ok=True)
for engine in ("kokoro", "piper") if config.PIPER_MODEL else ("kokoro",):
    config.VOICE_ENGINE = engine
    m = mouth.Mouth.__new__(mouth.Mouth)          # skip __init__: no audio device needed
    if engine == "piper":
        from piper import PiperVoice
        m.piper = PiperVoice.load(config.PIPER_MODEL)
    else:
        m.kokoro = mouth.Kokoro(config.KOKORO_MODEL, config.KOKORO_VOICES)
    audio = m._synth(text)
    assert audio.ndim == 1 and len(audio) > mouth.RATE, f"{engine}: no audio"
    with wave.open(f"logs/voice-{engine}.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(mouth.RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    print(engine, f"{len(audio) / mouth.RATE:.2f}s ok")
