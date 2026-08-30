import os

from dotenv import load_dotenv
from google import genai


load_dotenv(".env")

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY bulunamadı.")

with genai.Client(api_key=api_key) as client:
    print("Erişilebilir modeller:\n")

    for model in client.models.list():
        name = getattr(model, "name", "")
        actions = getattr(model, "supported_actions", []) or []

        if "generateContent" in actions or "generate_content" in actions:
            print(name)