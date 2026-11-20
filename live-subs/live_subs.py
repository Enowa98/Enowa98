"""
Live Subs: real-time Russian subtitles + translation for anything your PC plays
(Twitch streams, Dota 2 voice chat, YouTube...).

How it works:
  1. Records your speakers/headphones output (WASAPI loopback). Your own microphone is NOT recorded.
  2. Cuts the audio into short pieces at natural pauses.
  3. Whisper (speech recognition, runs on your NVIDIA GPU) turns each piece into Russian text.
  4. The text is translated and shown in a small always-on-top window.
  5. Everything is also saved to transcripts/<date>.txt so you can study it later.

Run:  run.bat                 (Chinese translation)
      run.bat --target en     (English translation)
      run.bat --help          (all options)
"""
import argparse
import datetime
import os
import queue
import sys
import threading
import time

import numpy as np

SAMPLE_RATE = 16000          # Whisper expects 16 kHz mono audio
FRAME = 480                  # 30 ms analysis frames

# Whisper sometimes "hears" these phrases in music or silence, because they appear at the end
# of many Russian videos it was trained on. Drop them.
HALLUCINATIONS = [
    "продолжение следует", "субтитры", "редактор субтитров", "спасибо за просмотр",
    "подписывайтесь на канал", "ставьте лайки", "корректор", "dimatorzok", "амара",
    "до новых встреч", "благодарю за внимание",
]

# Hints that help Whisper spell game words correctly.
DOTA_PROMPT = ("Стрим по доте. Мид, керри, саппорт, ульта, бкб, рошан, крипы, варды, ганг, "
               "фарм, тиник, шейкер, морф, пудж, инвокер, братан, чат.")

TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
            "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
            "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
            "щ": "shch", "ъ": "", "ы": "y", "ь": "'", "э": "e", "ю": "yu", "я": "ya"}


def translit(text):
    out = []
    for ch in text:
        lo = ch.lower()
        if lo in TRANSLIT:
            r = TRANSLIT[lo]
            out.append(r.capitalize() if ch != lo else r)
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------- GPU setup
def add_cuda_dll_paths():
    """On Windows, the pip packages nvidia-cublas-cu12 / nvidia-cudnn-cu12 put their DLLs in
    folders Python doesn't search by default. Add them so the GPU can be used."""
    if os.name != "nt":
        return
    import importlib.util
    for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError):
            continue
        if not spec or not spec.submodule_search_locations:
            continue
        for base in spec.submodule_search_locations:
            dll_dir = os.path.join(base, "bin")
            if os.path.isdir(dll_dir):
                os.add_dll_directory(dll_dir)
                os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")


def load_model(name, device, status):
    from faster_whisper import WhisperModel
    attempts = []
    if device in ("auto", "cuda"):
        attempts.append((name if name != "auto" else "large-v3-turbo", "cuda", "float16"))
    if device in ("auto", "cpu"):
        attempts.append((name if name != "auto" else "small", "cpu", "int8"))
    last_err = None
    for model_name, dev, compute in attempts:
        status(f"Loading {model_name} on {dev.upper()}… (first run downloads it, 0.5–1.5 GB)")
        try:
            model = WhisperModel(model_name, device=dev, compute_type=compute)
            # Run once on silence so a broken GPU setup fails here, not mid-stream.
            list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="ru")[0])
            return model, model_name, dev
        except Exception as e:  # noqa: BLE001 - show any loading problem to the user
            last_err = e
            print(f"[model] {model_name} on {dev} failed: {e}", file=sys.stderr)
    raise RuntimeError(f"Could not load a speech model: {last_err}")


# ---------------------------------------------------------------- Audio capture
def list_loopback_devices():
    import pyaudiowpatch as pyaudio
    with pyaudio.PyAudio() as p:
        print("Loopback devices (use the number with --device-index):")
        for d in p.get_loopback_device_info_generator():
            print(f"  [{d['index']}] {d['name']}")


def to_16k_mono(data, channels, rate):
    x = np.frombuffer(data, dtype=np.float32)
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate == SAMPLE_RATE:
        return x
    if rate % SAMPLE_RATE == 0:                      # e.g. 48000 -> 16000: average every 3 samples
        k = rate // SAMPLE_RATE
        n = len(x) // k * k
        return x[:n].reshape(-1, k).mean(axis=1)
    n_out = int(len(x) * SAMPLE_RATE / rate)          # e.g. 44100 -> 16000
    return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)


class Segmenter:
    """Collects audio and cuts it at the quietest moment once it is long enough.
    Works even with game music in the background, because it looks for a *relative* dip
    in loudness rather than true silence."""

    def __init__(self, min_sec=2.5, max_sec=7.0):
        self.min = int(min_sec * SAMPLE_RATE)
        self.max = int(max_sec * SAMPLE_RATE)
        self.buf = np.zeros(0, dtype=np.float32)

    def push(self, samples):
        self.buf = np.concatenate([self.buf, samples])
        out = []
        while len(self.buf) >= self.min:
            cut = self._find_cut()
            if cut is None:
                break
            out.append(self.buf[:cut])
            self.buf = self.buf[cut:]
        return out

    def _find_cut(self):
        n = len(self.buf) // FRAME
        rms = np.sqrt(np.mean(self.buf[: n * FRAME].reshape(n, FRAME) ** 2, axis=1) + 1e-12)
        smooth = np.convolve(rms, np.ones(10) / 10, mode="same")       # ~300 ms window
        start = self.min // FRAME
        if n - start < 12:
            return None if len(self.buf) < self.max else len(self.buf)
        region = smooth[start : n - 5]
        i = int(np.argmin(region))
        if region[i] < 0.5 * float(np.median(rms)) or len(self.buf) >= self.max:
            return (start + i) * FRAME
        return None


def capture_loop(device_index, out_q, paused, stop, status):
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    try:
        if device_index is None:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            dev = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not dev.get("isLoopbackDevice"):
                for lb in p.get_loopback_device_info_generator():
                    if dev["name"] in lb["name"]:
                        dev = lb
                        break
                else:
                    raise RuntimeError("No loopback device found. Try run.bat --list-devices")
        else:
            dev = p.get_device_info_by_index(device_index)
        channels, rate = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"])
        seg = Segmenter()

        def callback(data, frame_count, time_info, flags):
            if not paused.is_set():
                for piece in seg.push(to_16k_mono(data, channels, rate)):
                    out_q.put(piece)
            return (None, pyaudio.paContinue)

        stream = p.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                        input_device_index=dev["index"], frames_per_buffer=int(rate * 0.05),
                        stream_callback=callback)
        status(f"Listening to: {dev['name']}")
        while not stop.is_set():
            time.sleep(0.2)
        stream.stop_stream()
        stream.close()
    finally:
        p.terminate()


# ---------------------------------------------------------------- Speech -> text
def is_junk(text):
    t = text.lower().strip(" .!?…")
    if len(t) < 2:
        return True
    return any(h in t for h in HALLUCINATIONS)


SLAVIC = ("ru", "uk", "be")


def transcribe(model, audio, lang, prompt, only_lang=True):
    """Returns (text, skipped_language). With only_lang, audio that Whisper doesn't hear as
    Russian (or Ukrainian/Belarusian, which it confuses with Russian) is skipped, so English ads,
    music or a Discord call in another language are not shown or sent for translation."""
    if float(np.sqrt(np.mean(audio ** 2))) < 0.002:          # basically silent
        return "", None
    opts = dict(beam_size=3, vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False, without_timestamps=True, initial_prompt=prompt)
    if lang != "auto" and only_lang:
        segments, info = model.transcribe(audio, language=None, **opts)   # detect first (cheap)
        if not info.duration_after_vad:
            return "", None
        probs = dict(info.all_language_probs or [])
        if sum(probs.get(code, 0.0) for code in SLAVIC) < 0.3:
            return "", info.language
        if info.language != lang:                                       # e.g. heard as "uk": redo as Russian
            segments, info = model.transcribe(audio, language=lang, **opts)
    else:
        segments, info = model.transcribe(audio, language=None if lang == "auto" else lang, **opts)
    parts = [s.text.strip() for s in segments
             if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
    text = " ".join(p for p in parts if p).strip()
    return ("" if is_junk(text) else text), None


LANG_NAMES = {"zh-CN": "Simplified Chinese", "zh-TW": "Traditional Chinese", "en": "English"}


class GoogleBackend:
    """Free Google Translate (no key). It blocks you for a while if you send too many requests,
    so lines are sent in batches, at most one request every 1.5 s, and after a block we wait
    and use MyMemory (another free service) in the meantime."""
    name = "Google"

    def __init__(self, target):
        from deep_translator import GoogleTranslator
        self.google = GoogleTranslator(source="ru", target=target)
        self.target = target
        self.cooldown_until = 0.0
        self.last = 0.0
        self.mymemory = None

    def translate(self, lines, context):
        joined = "\n".join(lines)
        if time.time() >= self.cooldown_until:
            wait = 1.5 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                out = self.google.translate(joined) or ""
                parts = out.split("\n")
                return parts if len(parts) == len(lines) else [out] + [""] * (len(lines) - 1)
            except Exception as e:  # noqa: BLE001
                if "TooManyRequests" not in type(e).__name__ and "429" not in str(e):
                    raise
                self.cooldown_until = time.time() + 120
                print("[translate] Google is rate-limiting; pausing it for 2 minutes, using MyMemory")
        return self._mymemory(lines)

    def _mymemory(self, lines):
        from deep_translator import MyMemoryTranslator
        if self.mymemory is None:
            target = {"en": "en-GB", "zh-CN": "zh-CN", "zh-TW": "zh-TW"}.get(self.target, self.target)
            self.mymemory = MyMemoryTranslator(source="ru-RU", target=target)
        return [self.mymemory.translate(line) or "" for line in lines]


class ClaudeBackend:
    """Claude translation: understands gamer slang, swearing and context from earlier lines.
    Needs an Anthropic API key in the ANTHROPIC_API_KEY environment variable (paid per use)."""

    def __init__(self, target, model):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.name = "Claude"
        lang = LANG_NAMES.get(target, target)
        self.system = (
            f"You translate live Russian speech into {lang} for a learner watching Russian Twitch "
            "streams and playing Dota 2. The text comes from automatic speech recognition, so it can "
            "contain recognition mistakes: translate what the speaker most likely meant. Keep the tone; "
            "translate swearing honestly, not softened. Keep Dota terms recognisable (hero names, items). "
            "Reply with exactly one numbered line per input line, in the same order, like '1. ...', "
            "and nothing else.")

    def translate(self, lines, context):
        ctx = "\n".join(context[-6:]) or "(none)"
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
        kwargs = dict(model=self.model, max_tokens=2048, system=self.system,
                      output_config={"effort": "low"},
                      messages=[{"role": "user", "content":
                                 f"Earlier lines, for context only:\n{ctx}\n\nTranslate:\n{numbered}"}])
        if "haiku" not in self.model:   # server-side fallback if a request is declined
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        while True:
            try:
                resp = self.client.beta.messages.create(**kwargs)
                break
            except self.anthropic.RateLimitError as e:
                time.sleep(int(e.response.headers.get("retry-after", "10")))
        if resp.stop_reason == "refusal":
            return ["(Claude declined to translate this)"] + [""] * (len(lines) - 1)
        text = "".join(b.text for b in resp.content if b.type == "text")
        found = {}
        for row in text.splitlines():
            head, _, rest = row.strip().partition(". ")
            if head.isdigit() and rest:
                found[int(head)] = rest.strip()
        return [found.get(i + 1, "") for i in range(len(lines))]


def make_backend(args):
    if args.target == "none" or args.translator == "none":
        return None
    use_claude = args.translator == "claude" or (
        args.translator == "auto" and os.environ.get("ANTHROPIC_API_KEY"))
    if use_claude:
        return ClaudeBackend(args.target, args.claude_model)
    return GoogleBackend(args.target)


def translation_loop(backend, tr_q, ui_q, log_path, stop):
    """Translates lines in small batches so the translation service gets fewer requests."""
    context = []
    while not stop.is_set():
        try:
            batch = [tr_q.get(timeout=0.5)]
        except queue.Empty:
            continue
        time.sleep(0.4)                                   # let lines that arrive together share a request
        while len(batch) < 6:
            try:
                batch.append(tr_q.get_nowait())
            except queue.Empty:
                break
        texts = [b[1] for b in batch]
        try:
            results = backend.translate(texts, context) if backend else [""] * len(batch)
        except Exception as e:  # noqa: BLE001
            msg = getattr(e, "message", None) or e.__class__.__name__
            results = [f"(translation failed: {msg})"] * len(batch)
            print(f"[translate] {backend.name} error: {e}", file=sys.stderr)
        context = (context + texts)[-10:]
        with open(log_path, "a", encoding="utf-8") as f:
            for (line_id, text, stamp), tr in zip(batch, results):
                if backend:
                    ui_q.put(("tr", line_id, tr))
                f.write(f"[{stamp}] {text}\n" + (f"           → {tr}\n" if tr else ""))
                print(f"[{stamp}] {text}" + (f"\n           → {tr}" if tr else ""))


# ---------------------------------------------------------------- Window
def run_window(args, ui_q, paused, stop):
    import tkinter as tk

    BG, RU, TR, MUTED = "#14171d", "#f2f3f5", "#ffd479", "#8b93a3"
    state = {"size": args.font, "translit": not args.no_translit, "lines": []}

    root = tk.Tk()
    root.title("Live Subs")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", args.opacity)
    root.configure(bg=BG)
    sw = root.winfo_screenwidth()
    root.geometry(f"760x260+{(sw - 760) // 2}+60")

    bar = tk.Frame(root, bg=BG)
    bar.pack(fill="x", padx=10, pady=(6, 0))
    status_lbl = tk.Label(bar, text="Starting…", fg=MUTED, bg=BG, font=("Segoe UI", 9), anchor="w")
    status_lbl.pack(side="left", fill="x", expand=True)

    def button(text, cmd):
        b = tk.Label(bar, text=text, fg=MUTED, bg=BG, font=("Segoe UI", 10, "bold"), cursor="hand2", padx=6)
        b.bind("<Button-1>", lambda e: cmd())
        b.pack(side="right")
        return b

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=12, pady=(2, 10))

    def render():
        for w in body.winfo_children():
            w.destroy()
        wrap = max(200, root.winfo_width() - 30)
        s = state["size"]
        for item in state["lines"][-args.lines:]:
            tk.Label(body, text=item["ru"], fg=RU, bg=BG, font=("Segoe UI", s, "bold"),
                     wraplength=wrap, justify="left", anchor="w").pack(fill="x")
            if state["translit"]:
                tk.Label(body, text=translit(item["ru"]), fg=MUTED, bg=BG, font=("Consolas", max(8, s - 6)),
                         wraplength=wrap, justify="left", anchor="w").pack(fill="x")
            if item.get("tr"):
                tk.Label(body, text=item["tr"], fg=TR, bg=BG, font=("Microsoft YaHei UI", max(9, s - 2)),
                         wraplength=wrap, justify="left", anchor="w").pack(fill="x", pady=(0, 6))

    def close():
        stop.set()
        root.destroy()

    def toggle_pause():
        if paused.is_set():
            paused.clear()
            pause_btn.config(text="⏸")
        else:
            paused.set()
            pause_btn.config(text="▶")
            status_lbl.config(text="Paused")

    def font(delta):
        state["size"] = min(40, max(10, state["size"] + delta))
        render()

    def toggle_translit():
        state["translit"] = not state["translit"]
        render()

    button("✕", close)
    pause_btn = button("⏸", toggle_pause)
    button("A+", lambda: font(2))
    button("A−", lambda: font(-2))
    button("abc", toggle_translit)

    # Drag anywhere to move; drag the bottom-right corner to resize.
    drag = {}

    def start_drag(e):
        drag.update(x=e.x_root, y=e.y_root, gx=root.winfo_x(), gy=root.winfo_y(),
                    w=root.winfo_width(), h=root.winfo_height(),
                    resize=e.x_root - root.winfo_x() > root.winfo_width() - 24
                    and e.y_root - root.winfo_y() > root.winfo_height() - 24)

    def on_drag(e):
        dx, dy = e.x_root - drag["x"], e.y_root - drag["y"]
        if drag["resize"]:
            root.geometry(f"{max(300, drag['w'] + dx)}x{max(120, drag['h'] + dy)}")
        else:
            root.geometry(f"+{drag['gx'] + dx}+{drag['gy'] + dy}")

    root.bind("<ButtonPress-1>", start_drag)
    root.bind("<B1-Motion>", on_drag)
    root.bind("<ButtonRelease-1>", lambda e: render() if drag.get("resize") else None)
    grip = tk.Label(root, text="◢", fg=MUTED, bg=BG, font=("Segoe UI", 9), cursor="size_nw_se")
    grip.place(relx=1.0, rely=1.0, anchor="se")
    root.bind("<Escape>", lambda e: close())

    def poll():
        changed = False
        try:
            while True:
                kind, a, b = ui_q.get_nowait()
                if kind == "status":
                    status_lbl.config(text=a)
                elif kind == "ru":
                    state["lines"].append({"id": a, "ru": b})
                    state["lines"] = state["lines"][-20:]
                    changed = True
                elif kind == "tr":
                    for item in state["lines"]:
                        if item["id"] == a:
                            item["tr"] = b
                    changed = True
                elif kind == "fatal":
                    status_lbl.config(text=a, fg="#ff7b6b")
        except queue.Empty:
            pass
        if changed:
            render()
        if not stop.is_set():
            root.after(100, poll)

    root.after(100, poll)
    root.mainloop()
    stop.set()


# ---------------------------------------------------------------- Main
def worker(args, ui_q, paused, stop):
    def status(msg):
        print(msg)
        ui_q.put(("status", msg, None))

    try:
        add_cuda_dll_paths()
        model, model_name, dev = load_model(args.model, args.device, status)
        backend = make_backend(args)
        audio_q = queue.Queue()
        threading.Thread(target=capture_loop, args=(args.device_index, audio_q, paused, stop, status),
                         daemon=True).start()
    except Exception as e:  # noqa: BLE001
        ui_q.put(("fatal", f"Error: {e}", None))
        print(f"Error: {e}", file=sys.stderr)
        return

    os.makedirs("transcripts", exist_ok=True)
    log_path = os.path.join("transcripts", datetime.date.today().isoformat() + ".txt")
    prompt = DOTA_PROMPT if not args.no_prompt else None
    tr_q = queue.Queue()
    threading.Thread(target=translation_loop, args=(backend, tr_q, ui_q, log_path, stop), daemon=True).start()
    tr_name = backend.name if backend else "no translation"
    n = 0
    while not stop.is_set():
        try:
            audio = audio_q.get(timeout=0.5)
        except queue.Empty:
            continue
        t0 = time.time()
        try:
            text, skipped = transcribe(model, audio, args.lang, prompt, not args.any_language)
        except Exception as e:  # noqa: BLE001
            status(f"Recognition error: {e}")
            continue
        if skipped and not paused.is_set():
            status(f"Skipped speech in another language ({skipped}) · {tr_name}")
        if not text:
            continue
        n += 1
        line_id = n
        ui_q.put(("ru", line_id, text))
        if not paused.is_set():
            status(f"{model_name} · {dev.upper()} · {time.time() - t0:.1f}s · {tr_name} · log: {log_path}")
        tr_q.put((line_id, text, datetime.datetime.now().strftime("%H:%M:%S")))


def main():
    ap = argparse.ArgumentParser(description="Live Russian subtitles for anything your PC plays.")
    ap.add_argument("--target", default="zh-CN", help="translation language: zh-CN (default), en, or none")
    ap.add_argument("--translator", default="auto", choices=["auto", "google", "claude", "none"],
                    help="auto = Claude if ANTHROPIC_API_KEY is set, otherwise free Google Translate")
    ap.add_argument("--claude-model", default="claude-opus-5-5",
                    help="Claude model for translation, e.g. claude-opus-5-5 (default) or claude-haiku-5-5 (much cheaper)")
    ap.add_argument("--lang", default="ru", help="spoken language: ru (default), uk, or auto")
    ap.add_argument("--model", default="auto", help="Whisper model: auto, large-v3-turbo, medium, small")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--device-index", type=int, default=None, help="loopback device number (see --list-devices)")
    ap.add_argument("--list-devices", action="store_true", help="show audio devices and exit")
    ap.add_argument("--font", type=int, default=18, help="font size")
    ap.add_argument("--lines", type=int, default=3, help="how many lines to show")
    ap.add_argument("--opacity", type=float, default=0.88, help="window opacity 0.3-1.0")
    ap.add_argument("--no-translit", action="store_true", help="hide the Latin-letter pronunciation line")
    ap.add_argument("--any-language", action="store_true",
                    help="don't skip speech that isn't Russian (by default it is ignored and not translated)")
    ap.add_argument("--no-prompt", action="store_true", help="don't give Whisper the Dota word hints")
    args = ap.parse_args()

    if args.list_devices:
        list_loopback_devices()
        return
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ui_q, paused, stop = queue.Queue(), threading.Event(), threading.Event()
    threading.Thread(target=worker, args=(args, ui_q, paused, stop), daemon=True).start()
    run_window(args, ui_q, paused, stop)


if __name__ == "__main__":
    main()
