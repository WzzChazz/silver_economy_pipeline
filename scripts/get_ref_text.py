import sys
import os
import whisper
import warnings
warnings.filterwarnings("ignore")

model = whisper.load_model("small")
res = model.transcribe("/Users/mr.wu/Downloads/uts_TASK_1781517629798_11f810af.mp3", fp16=False, language="zh")
print("TRANSCRIPT:")
print(res["text"])
