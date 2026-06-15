import whisper
import imageio_ffmpeg
import numpy as np
import subprocess

def _load_audio(file, sr=16000):
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-threads", "0",
        "-i", file, "-f", "s16le", "-ac", "1",
        "-acodec", "pcm_s16le", "-ar", str(sr), "-"
    ]
    out = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0

whisper.audio.load_audio = _load_audio
model = whisper.load_model("small")
audio_path = "/Users/mac/Downloads/uts_TASK_1781517629798_11f810af.mp3"
result = model.transcribe(audio_path, fp16=False, language="zh", word_timestamps=True)

timestamps = []
current_phrase = ""
start_t = None
end_t = None

for seg in result["segments"]:
    for w in seg.get("words", []):
        word = w["word"].strip()
        if not word: continue
        
        if start_t is None:
            start_t = w["start"]
        end_t = w["end"]
        current_phrase += word
        
        if any(p in word for p in ["，", "。", "！", "？", ",", ".", "?", "!"]) or len(current_phrase) > 12:
            timestamps.append({"text": current_phrase, "start": start_t, "end": end_t})
            current_phrase = ""
            start_t = None

if current_phrase:
    timestamps.append({"text": current_phrase, "start": start_t, "end": end_t})

for t in timestamps:
    print(t)
