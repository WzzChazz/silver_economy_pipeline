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
result = model.transcribe("/Users/mac/Downloads/uts_TASK_1781517629798_11f810af.mp3", fp16=False, language="zh")

print("--- TRANSCRIBED TEXT ---")
for seg in result["segments"]:
    print(seg["text"])

