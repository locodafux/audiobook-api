from huggingface_hub import snapshot_download
import os

print("📂 Downloading all Kokoro voices to your Mac...")

# This targets the 'voices' folder specifically
snapshot_download(
    repo_id="hexgrad/Kokoro-82M", 
    allow_patterns=["voices/*.pt", "*.pth", "config.json"]
)

print("\n✅ All voices downloaded! You can now use them offline.")
