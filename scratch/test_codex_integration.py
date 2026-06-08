import os
import sys
import base64

sys.path.insert(0, "/Users/rafaelordanini/Desktop/HQ")

# Force using absolute path for .env loading if agents.py requires it
from dotenv import load_dotenv
load_dotenv(dotenv_path="/Users/rafaelordanini/Desktop/HQ/.env")

from app.agents import _generar_imagem_codex

def test_codex():
    print("Testing Codex CLI image generation wrapper...")
    prompt = "A simple ink wash style painting of a taoist hermit, traditional Chinese art style."
    try:
        res = _generar_imagem_codex(prompt)
        print("Success! Got result dictionary.")
        print("URL:", res.get("url"))
        b64 = res.get("b64_json")
        if b64:
            print(f"Base64 data length: {len(b64)}")
            output_path = "/Users/rafaelordanini/Desktop/HQ/scratch/test_codex_output.png"
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(b64))
            print(f"Saved generated image to: {output_path}")
            
            # Check dimensions using PIL
            from PIL import Image
            img = Image.open(output_path)
            print(f"Image format: {img.format}, size: {img.size}")
        else:
            print("ERROR: No base64 data returned!")
    except Exception as e:
        print("Codex generation failed with exception:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_codex()
