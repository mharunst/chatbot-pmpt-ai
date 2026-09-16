# Chatbot RAG Penjamin Mutu Perguruan Tinggi

## Tahap 1

Project ini saat ini sengaja baru menangani:

1. PDF → Markdown dengan LlamaParse
2. Evaluasi hasil parsing
3. Page Mapping untuk provenance/citation

Belum ada:

- chunking
- embedding
- vector database
- retrieval
- LLM answering

## Struktur

```text
chatbot-rag-llamaparse-tahap-1-stage-1-5-v2/
├── data/
│   ├── input/
│   ├── parsed/
│   └── metadata/
├── src/
│   └── parsing/
│       ├── parse_pdf.py
│       └── page_mapping.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Persiapan

Buat virtual environment:

```bash
python -m venv .venv
```

Aktifkan di Windows:

```bash
.venv\Scripts\activate
```

Install dependency:

```bash
pip install -r requirements.txt
```

Salin `.env.example` menjadi `.env`, lalu isi:

```text
LLAMA_CLOUD_API_KEY=API_KEY_ANDA
```

## Stage 1 - Parsing

Letakkan PDF di:

```text
data/input/Buku IKU Diktisaintek Berdampak_V1.pdf
```

Jalankan:

```bash
python src/parsing/parse_pdf.py
```

Jika Markdown sudah ada, script akan melakukan SKIP sehingga LlamaParse tidak dipanggil ulang.

Output:

```text
data/parsed/Buku_IKU_Diktisaintek_Berdampak_V1.md
```

## Tahap berikutnya

```text
Stage 1
PDF → Markdown
       ↓
Stage 1.5
PDF page → printed page mapping
       ↓
Stage 2
Preprocessing
       ↓
Stage 3
Chunking + metadata
       ↓
Stage 4
Embedding
       ↓
Stage 5
Vector DB
       ↓
Stage 6
Retrieval
       ↓
Stage 7
LLM + citation
```
