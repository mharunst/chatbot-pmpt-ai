from pathlib import Path
import os

from dotenv import load_dotenv
from llama_cloud import LlamaCloud

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_ROOT / "data" / "input"
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"

PDF_PATH = INPUT_DIR / "PPT IKU Diktisaintek Berdampak_PTS V2.pdf"
OUTPUT_PATH = PARSED_DIR / "PPT_IKU_Diktisaintek_Berdampak_PTS_V2.md"


def main():
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLAMA_CLOUD_API_KEY belum ditemukan. "
            "Buat file .env dari .env.example."
        )

    if not PDF_PATH.exists():
        raise FileNotFoundError(f"PDF tidak ditemukan: {PDF_PATH}")

    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    if OUTPUT_PATH.exists():
        print(f"[SKIP] Output sudah ada: {OUTPUT_PATH}")
        print("[INFO] LlamaParse tidak dipanggil ulang.")
        return

    print(f"[INFO] Upload: {PDF_PATH.name}")

    client = LlamaCloud(api_key=api_key)

    file = client.files.create(
        file=str(PDF_PATH),
        purpose="parse",
    )

    print("[INFO] Parsing dengan LlamaParse...")

    result = client.parsing.parse(
        file_id=file.id,
        tier="agentic plus",
        version="latest",
        expand=["markdown"],
    )

    markdown = "\n\n".join(
        page.markdown
        for page in result.markdown.pages
        if page.markdown
    )

    OUTPUT_PATH.write_text(markdown, encoding="utf-8")

    print(f"[OK] Markdown tersimpan: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
