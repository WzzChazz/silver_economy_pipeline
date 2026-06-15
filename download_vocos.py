from modelscope import snapshot_download
model_dir = snapshot_download('charactr/vocos-mel-24khz')
print(model_dir)
