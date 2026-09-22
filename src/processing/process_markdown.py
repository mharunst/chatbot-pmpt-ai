"""
process_markdown.py
Tahap PROCESSING pada pipeline RAG Penjaminan Mutu Perguruan Tinggi.
(pdf parsing -> [processing] -> chunking -> embedding -> vector db -> retrieval -> LLM)

Input : file markdown hasil LlamaParse (data/parsed/*.md)
Output: file JSON terstruktur per dokumen (data/processed/*_processed.json)
        berisi list of "blocks" (heading / paragraph / table / image_note)
        lengkap dengan metadata (source_file, page, section_path)

Tugas tahap ini (BUKAN chunking, chunking dilakukan di tahap berikutnya):
1. Membuang noise berulang: header/footer running text & nomor halaman
2. Membuang gambar dekoratif (logo/icon instansi yg berulang di tiap halaman),
   tapi TETAP menyimpan gambar informatif (flow_chart/diagram/image) sbg catatan teks
3. Menormalkan semua <table> HTML -> Markdown table yang konsisten
4. Melacak hierarki heading (BAB / sub-bab) sbg section_path -> penting utk sitasi
5. Melacak nomor halaman (dari marker page_N_image) -> penting utk sitasi
"""

import re
import json
from pathlib import Path
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konfigurasi noise & filter — sesuaikan bila menemukan pola lain di dokumen
# ---------------------------------------------------------------------------

NOISE_LINE_PATTERNS = [
    # footer berulang, polos/bold, dgn/tanpa nomor halaman menyatu di baris yg sama
    r"^\*{0,2}Indikator Kinerja Utama \(IKU\) Diktisaintek Berdampak\*{0,2}\s*\*{0,2}\d{0,3}\s*\|?\s*\*{0,2}$",
    r"^\*{0,2}\d{1,3}\s*\|?\*{0,2}$",   # nomor halaman berdiri sendiri: "14", "**14**", "**2 |**"
]

INLINE_IMAGE_RE = re.compile(r"!\[.*?\]\(.*?\)")

DECORATIVE_IMAGE_ALT_PREFIXES = ("logo:", "icon:")          # dibuang
INFORMATIVE_IMAGE_ALT_PREFIXES = ("flow_chart:", "diagram:", "image:", "photo:")  # disimpan

PAGE_MARKER_RE = re.compile(r"page_(\d+)_image")
LOCAL_PAGE_MARKER_RE = re.compile(r"^-\s*(\d+)\s*-$")  # penomoran halaman lokal di Lampiran, mis. "- 2 -"
IMAGE_LINE_RE = re.compile(r"^!\[(.*?)\]\((.*?)\)$")
HEADING_RE = re.compile(r"^(#{1,4})\s*(.+)$")
MD_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
MD_TABLE_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")


def html_table_to_markdown(html: str) -> str:
    """Konversi 1 blok <table>...</table> HTML menjadi Markdown table.
    Menangani colspan sederhana dengan mengulang isi sel; rowspan diabaikan
    (nilai hanya muncul di baris asalnya) -- cukup utk kebutuhan retrieval teks.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return ""

    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True)
            colspan = int(cell.get("colspan", 1))
            cells.extend([text] + [""] * (colspan - 1))
        rows.append(cells)

    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    header, *body = rows
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    for r in body:
        lines.append("| " + " | ".join(c.replace("\n", " ") for c in r) + " |")
    return "\n".join(lines)


def process_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    blocks: list[dict] = []
    current_page = 1
    last_image_anchor_page = 1   # halaman asli terakhir yg diketahui dari page_N_image
    local_page_offset = None     # offset aktif utk penomoran lokal "- N -" (Lampiran)
    local_page_prev = None       # nomor lokal terakhir yg terlihat (deteksi reset/lompat)
    section_stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    block_id = 0

    def section_path() -> str:
        return " > ".join(t for _, t in section_stack)

    def flush_paragraph():
        nonlocal buffer, block_id
        content = "\n".join(buffer).strip()
        buffer = []
        if not content:
            return
        block_id += 1
        blocks.append({
            "id": f"{path.stem}_{block_id}",
            "source_file": path.name,
            "page": current_page,
            "section_path": section_path(),
            "block_type": "paragraph",
            "content": content,
        })

    in_table = False
    table_buffer: list[str] = []

    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]
        line = raw_line.rstrip()
        stripped = line.strip()

        # 1) update nomor halaman dari marker gambar (anchor "asli") -- TIDAK skip baris,
        #    karena baris yg sama biasanya juga berisi gambar/heading yg perlu diproses lebih lanjut
        m = PAGE_MARKER_RE.search(line)
        if m:
            current_page = int(m.group(1))
            last_image_anchor_page = current_page
            local_page_offset = None
            local_page_prev = None

        # 1b) fallback: penomoran halaman lokal "- N -" yg dipakai di dokumen Lampiran/SK
        #     yg tidak punya gambar sama sekali (mis. halaman 98-141 pada Buku IKU).
        #     Setiap kali urutan lokal "reset" (bukan kelanjutan N sebelumnya+1),
        #     offset dihitung ulang dari anchor gambar asli terakhir yg diketahui.
        lm = LOCAL_PAGE_MARKER_RE.match(stripped)
        if lm:
            n_local = int(lm.group(1))
            if local_page_prev is None or n_local != local_page_prev + 1:
                local_page_offset = last_image_anchor_page
            local_page_prev = n_local
            current_page = local_page_offset + n_local
            i += 1
            continue

        # 2) buang baris noise (footer berulang, nomor halaman berdiri sendiri)
        if any(re.match(p, stripped) for p in NOISE_LINE_PATTERNS):
            i += 1
            continue

        # 3) tabel HTML
        if stripped.startswith("<table>"):
            flush_paragraph()
            in_table = True
            table_buffer = [line]
            i += 1
            continue
        if in_table:
            table_buffer.append(line)
            if "</table>" in line:
                in_table = False
                md_table = html_table_to_markdown("\n".join(table_buffer))
                if md_table:
                    block_id += 1
                    blocks.append({
                        "id": f"{path.stem}_{block_id}",
                        "source_file": path.name,
                        "page": current_page,
                        "section_path": section_path(),
                        "block_type": "table",
                        "table_source": "html",
                        "content": md_table,
                    })
            i += 1
            continue

        # 3b) tabel Markdown pipe (LlamaParse kadang mengeluarkan tabel format
        #     ini, bukan <table> HTML) -> deteksi: baris "| ... |" diikuti baris
        #     separator "| --- | :--- |" dst, lalu kumpulkan baris pipe berikutnya
        if (MD_TABLE_ROW_RE.match(stripped) and i + 1 < n
                and MD_TABLE_SEP_RE.match(lines[i + 1].strip())):
            flush_paragraph()
            md_rows = [stripped, lines[i + 1].strip()]
            j = i + 2
            while j < n and MD_TABLE_ROW_RE.match(lines[j].strip()):
                md_rows.append(lines[j].strip())
                j += 1
            block_id += 1
            blocks.append({
                "id": f"{path.stem}_{block_id}",
                "source_file": path.name,
                "page": current_page,
                "section_path": section_path(),
                "block_type": "table",
                "table_source": "markdown_pipe",
                "content": "\n".join(md_rows),
            })
            i = j
            continue

        # 4) gambar: buang dekoratif, simpan yang informatif sbg catatan teks
        img_match = IMAGE_LINE_RE.match(stripped)
        if not img_match and INLINE_IMAGE_RE.search(stripped):
            # gambar menyatu di tengah baris teks (heading/paragraf) -> buang syntax-nya saja,
            # sisa teks di baris tetap diproses seperti biasa
            line = INLINE_IMAGE_RE.sub("", line).strip()
            stripped = line.strip()
        if img_match:
            alt = img_match.group(1).strip()
            if alt.lower().startswith(DECORATIVE_IMAGE_ALT_PREFIXES):
                i += 1
                continue
            if alt.lower().startswith(INFORMATIVE_IMAGE_ALT_PREFIXES) or alt:
                flush_paragraph()
                block_id += 1
                blocks.append({
                    "id": f"{path.stem}_{block_id}",
                    "source_file": path.name,
                    "page": current_page,
                    "section_path": section_path(),
                    "block_type": "image_note",
                    "content": alt,
                })
            i += 1
            continue

        # 5) heading -> update section_stack
        h_match = HEADING_RE.match(stripped)
        if h_match:
            flush_paragraph()
            level = len(h_match.group(1))
            title = re.sub(r"\*+", "", h_match.group(2)).strip()
            # bersihkan sisa kurung kosong akibat penanda footnote "(*)" yg asteriknya terbuang,
            # lalu rapikan spasi ganda yg mungkin tertinggal
            title = re.sub(r"\(\s*\)", "", title)
            title = re.sub(r"\s{2,}", " ", title).strip()
            if title:
                section_stack = [s for s in section_stack if s[0] < level]
                section_stack.append((level, title))
                block_id += 1
                blocks.append({
                    "id": f"{path.stem}_{block_id}",
                    "source_file": path.name,
                    "page": current_page,
                    "section_path": section_path(),
                    "block_type": "heading",
                    "content": title,
                })
            i += 1
            continue

        # 6) baris kosong -> pemisah paragraf, selain itu masuk buffer
        if stripped == "":
            flush_paragraph()
        else:
            buffer.append(line)
        i += 1

    flush_paragraph()
    return blocks


def main():
    input_dir = Path("data/parsed")
    output_dir = Path("data/processed")
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for md_file in sorted(input_dir.glob("*.md")):
        blocks = process_file(md_file)
        total += len(blocks)
        out_path = output_dir / f"{md_file.stem}_processed.json"
        out_path.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")

        counts: dict[str, int] = {}
        for b in blocks:
            counts[b["block_type"]] = counts.get(b["block_type"], 0) + 1
        print(f"{md_file.name}: {len(blocks)} blocks {counts} -> {out_path}")

    print(f"Total blocks (semua dokumen): {total}")


if __name__ == "__main__":
    main()
