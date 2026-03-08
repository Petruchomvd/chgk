"""List promising OpenRouter models for classification benchmark."""
import os
from pathlib import Path

env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

import requests

key = os.environ.get("OPENROUTER_API_KEY", "")
resp = requests.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {key}"})
models = resp.json().get("data", [])

keywords = ["llama-3.3", "llama-4", "sonnet", "gemma-3", "mistral-large", "command-r", "jamba", "phi-4"]

for m in models:
    name = m["id"].lower()
    pricing = m.get("pricing", {})
    prompt_price = float(pricing.get("prompt", "0"))
    comp_price = float(pricing.get("completion", "0"))

    if any(kw in name for kw in keywords):
        print(f"{m['id']:55s} ${prompt_price*1e6:8.2f}/M in  ${comp_price*1e6:8.2f}/M out")
