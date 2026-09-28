"""ARIA mic diagnostic — run with: python aria_mic_diag.py
Tests: mic device list, 5s raw recording, noise level, Google speech-to-text.
Paste the full output back so the failure can be fixed."""
import traceback

print("=== step 1: import speech_recognition ===")
try:
    import speech_recognition as sr
    print("ok, version:", sr.__version__)
except Exception:
    print("FAILED to import speech_recognition:")
    traceback.print_exc()
    raise SystemExit(1)

print("\n=== step 2: list microphone devices ===")
try:
    names = sr.Microphone.list_microphone_names()
    for i, n in enumerate(names):
        print(f"  [{i}] {n}")
    if not names:
        print("  !! NO MICROPHONES FOUND BY WINDOWS")
except Exception:
    print("FAILED to list mics:")
    traceback.print_exc()

print("\n=== step 3: open default mic and record 5s (say something!) ===")
r = sr.Recognizer()
try:
    with sr.Microphone() as source:
        print("mic opened:", source)
        r.adjust_for_ambient_noise(source, duration=1)
        print("energy threshold:", r.energy_threshold)
        print("recording... say 'Aria hello test'")
        audio = r.record(source, duration=5)
    print("recorded OK, length bytes:", len(audio.get_wav_data()))
except Exception:
    print("FAILED to record:")
    traceback.print_exc()
    raise SystemExit(1)

print("\n=== step 4: loudness check (was the mic picking up ANY sound?) ===")
import audioop
raw = audio.get_raw_data()
rms = audioop.rms(raw, 2)
print("RMS level:", rms, "(near 0 = mic is silent/dead, hundreds+ = sound present)")

print("\n=== step 5: Google speech-to-text ===")
try:
    text = r.recognize_google(audio)
    print("TRANSCRIPT:", repr(text))
except sr.UnknownValueError:
    print("Google heard audio but could not understand any words (garbage/unclear).")
except sr.RequestError as e:
    print("FAILED to reach Google STT (network/API issue):", e)
except Exception:
    print("UNEXPECTED failure during recognition:")
    traceback.print_exc()

print("\n=== done — paste ALL of the above output back ===")