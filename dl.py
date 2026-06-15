from modelscope.hub.file_download import model_file_download
import shutil
import os

try:
    path = model_file_download(model_id='SWivid/F5-TTS', file_path='F5TTS_Base/vocab.txt')
    shutil.copy(path, '/Users/mac/Downloads/f5_models/vocab.txt')
    print("vocab.txt downloaded successfully via ModelScope!")
except Exception as e:
    print(f"ModelScope failed: {e}")
