import os
import huggingface_hub
import torch
import torchaudio

LOCAL_MODEL_DIR = "/Users/mac/Downloads/f5_models"

def mock_hf_download(repo_id, filename, **kwargs):
    base_name = os.path.basename(filename)
    local_path = os.path.join(LOCAL_MODEL_DIR, base_name)
    if not os.path.exists(local_path):
        print(f"MISSING: {base_name} from {repo_id}/{filename}")
        # We create a dummy file just to see ALL missing files in one run
        open(local_path, "w").write("dummy")
    return local_path

huggingface_hub.hf_hub_download = mock_hf_download

from f5_tts.api import F5TTS

try:
    print("Testing F5TTS initialization...")
    f5tts = F5TTS()
except Exception as e:
    print("Initialization hit an error (expected if dummy files were used):", e)

# Clean up dummy files
for f in os.listdir(LOCAL_MODEL_DIR):
    path = os.path.join(LOCAL_MODEL_DIR, f)
    if os.path.getsize(path) == 5: # "dummy"
        os.remove(path)
