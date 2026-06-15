import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from huggingface_hub import hf_hub_download

print("Downloading vocos config...")
hf_hub_download(repo_id="charactr/vocos-mel-24khz", filename="config.yaml")
print("Downloading F5-TTS model...")
hf_hub_download(repo_id="SWivid/F5-TTS", filename="F5TTS_Base/model_1200000.safetensors")
print("Done!")
