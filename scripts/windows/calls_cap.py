"""calls_cap.py: stream one Windows audio source as raw PCM16 16 kHz mono to stdout.
usage: py calls_cap.py loop|mic [--list] [--seconds N]
loop = WASAPI loopback of the default render (output) device (far end of the call)
mic  = default WASAPI capture device (the laptop mic)"""
import sys, struct, time
import pyaudiowpatch as pa

def pick(p, which):
    wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
    if which == "loop":
        spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if spk.get("isLoopbackDevice"):
            return spk
        for lb in p.get_loopback_device_info_generator():
            if spk["name"] in lb["name"]:
                return lb
        raise SystemExit("no loopback for " + spk["name"])
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])

def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "loop"
    secs = float(sys.argv[sys.argv.index("--seconds") + 1]) if "--seconds" in sys.argv else 0
    p = pa.PyAudio()
    if "--list" in sys.argv:
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            sys.stderr.write(f"{i} {d['name']} in={d['maxInputChannels']} out={d['maxOutputChannels']} sr={d['defaultSampleRate']} host={d['hostApi']} lb={d.get('isLoopbackDevice')}\n")
    dev = pick(p, which)
    ch = max(1, int(dev["maxInputChannels"])); sr = int(dev["defaultSampleRate"])
    sys.stderr.write(f"capture {which}: {dev['name']} ch={ch} sr={sr}\n"); sys.stderr.flush()
    import numpy as np
    out = sys.stdout.buffer
    step = sr / 16000.0
    frames = int(sr / 10)  # 100 ms
    s = p.open(format=pa.paInt16, channels=ch, rate=sr, input=True, input_device_index=dev["index"], frames_per_buffer=frames)
    t0 = time.time(); pos = 0.0
    while True:
        data = s.read(frames, exception_on_overflow=False)
        a = np.frombuffer(data, dtype=np.int16).reshape(-1, ch).astype(np.float32).mean(axis=1)
        idx = np.arange(pos, len(a), step)
        pos = idx[-1] + step - len(a) if len(idx) else pos - len(a)
        y = a[idx.astype(np.int64)] if len(idx) else np.zeros(0, np.float32)
        out.write(np.clip(y, -32768, 32767).astype(np.int16).tobytes()); out.flush()
        if secs and time.time() - t0 > secs:
            break

if __name__ == "__main__":
    main()
