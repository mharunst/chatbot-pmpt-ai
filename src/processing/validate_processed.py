"""
validate_processed.py
Validasi otomatis untuk output tahap PROCESSING (data/processed/*.json)
terhadap markdown asli (data/parsed/*.md).

Cek yang dilakukan:
1. Kelengkapan konten kasar (word count) — deteksi potensi data hilang
2. Integritas tabel — bandingkan jumlah baris <table> asli vs baris markdown table hasil
3. Block kosong / anomali (mis. heading yang isinya kepanjangan / bukan heading asli)
4. Duplikasi block (content persis sama, berpotensi bug regex overlap)

Output: ringkasan ke stdout + detail issue ke JSON report per dokumen
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from process_markdown import (  # noqa: E402
    NOISE_LINE_PATTERNS, IMAGE_LINE_RE, DECORATIVE_IMAGE_ALT_PREFIXES, INLINE_IMAGE_RE,
    MD_TABLE_ROW_RE, MD_TABLE_SEP_RE, LOCAL_PAGE_MARKER_RE,
)

PARSED_DIR = Path("data/parsed")
PROCESSED_DIR = Path("data/processed")
REPORT_DIR = Path("data/validation")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def count_original_tables(md_text: str):
    """Ambil jumlah baris <tr> tiap <table> di markdown asli, berurutan."""
    tables = re.findall(r"<table>.*?</table>", md_text, flags=re.DOTALL)
    counts = []
    for t in tables:
        soup = BeautifulSoup(t, "html.parser")
        n_rows = len(soup.find_all("tr"))
        counts.append(n_rows)
    return counts


def count_md_table_rows(md_table_content: str) -> int:
    """Hitung baris (termasuk header) pada markdown table hasil processing."""
    lines = [l for l in md_table_content.strip().split("\n") if l.strip().startswith("|")]
    # baris ke-2 biasanya separator '---', tidak dihitung sbg data tapi tetap baris tabel
    return len(lines)


def validate_file(md_path: Path, json_path: Path) -> dict:
    md_text = md_path.read_text(encoding="utf-8")
    blocks = json.loads(json_path.read_text(encoding="utf-8"))

    issues = {
        "empty_or_short_blocks": [],
        "suspicious_headings": [],
        "duplicate_blocks": [],
        "table_row_mismatch": [],
    }

    # --- 1) Kelengkapan konten kasar ---
    # Bandingkan secara adil & robust: buang blok <table>...</table> UTUH lebih dulu
    # (regex, bukan line-by-line -- lebih tahan terhadap variasi format tag),
    # lalu hitung kata teks-non-tabel dan kata isi tabel (via get_text) secara terpisah.
    text_without_tables = re.sub(r"<table>.*?</table>", "", md_text, flags=re.DOTALL)

    nontable_parts = []
    pipe_table_parts = []
    src_lines = text_without_tables.split("\n")
    k = 0
    while k < len(src_lines):
        raw_line = src_lines[k]
        line = raw_line.strip()
        if any(re.match(p, line) for p in NOISE_LINE_PATTERNS):
            k += 1
            continue
        if LOCAL_PAGE_MARKER_RE.match(line):
            k += 1
            continue
        img_match = IMAGE_LINE_RE.match(line)
        if img_match:
            alt = img_match.group(1).strip()
            if alt.lower().startswith(DECORATIVE_IMAGE_ALT_PREFIXES):
                k += 1
                continue
            nontable_parts.append(alt)
            k += 1
            continue
        # tabel markdown-pipe (sama seperti deteksi di process_markdown.py) -> pisahkan
        # dari nontable, supaya sebanding dgn block_type='table' hasil processing
        if (MD_TABLE_ROW_RE.match(line) and k + 1 < len(src_lines)
                and MD_TABLE_SEP_RE.match(src_lines[k + 1].strip())):
            pipe_table_parts.append(line)
            pipe_table_parts.append(src_lines[k + 1].strip())
            k += 2
            while k < len(src_lines) and MD_TABLE_ROW_RE.match(src_lines[k].strip()):
                pipe_table_parts.append(src_lines[k].strip())
                k += 1
            continue
        # buang sintaks gambar inline yang menyatu di tengah baris (spt di process_markdown.py)
        nontable_parts.append(INLINE_IMAGE_RE.sub("", raw_line))
        k += 1

    original_nontable_wc = word_count("\n".join(nontable_parts))
    original_html_table_wc = sum(
        word_count(BeautifulSoup(t, "html.parser").get_text(" "))
        for t in re.findall(r"<table>.*?</table>", md_text, flags=re.DOTALL)
    )
    original_pipe_table_wc = word_count("\n".join(pipe_table_parts))
    original_table_wc = original_html_table_wc + original_pipe_table_wc
    original_wc = original_nontable_wc + original_table_wc

    processed_nontable_wc = sum(word_count(b["content"]) for b in blocks if b["block_type"] != "table")
    processed_table_wc = sum(word_count(b["content"]) for b in blocks if b["block_type"] == "table")
    processed_wc = processed_nontable_wc + processed_table_wc

    coverage_pct = round(processed_wc / max(original_wc, 1) * 100, 1)
    coverage_nontable_pct = round(processed_nontable_wc / max(original_nontable_wc, 1) * 100, 1)
    coverage_table_pct = round(processed_table_wc / max(original_table_wc, 1) * 100, 1)

    # --- 2) Integritas tabel (hanya bandingkan tabel yang berasal dari <table> HTML;
    #        tabel asal markdown-pipe sudah dalam format final sejak sumbernya) ---
    original_table_rows = count_original_tables(md_text)
    processed_tables = [b for b in blocks if b["block_type"] == "table"]
    processed_html_tables = [b for b in processed_tables if b.get("table_source") == "html"]
    processed_pipe_tables = [b for b in processed_tables if b.get("table_source") == "markdown_pipe"]
    mismatches = 0
    for i, b in enumerate(processed_html_tables):
        if i >= len(original_table_rows):
            break
        orig_rows = original_table_rows[i]
        new_rows = count_md_table_rows(b["content"])
        # markdown table hasil = header + separator + body -> body rows = new_rows - 2
        new_body_rows = max(new_rows - 2, 0)
        orig_body_rows = max(orig_rows - 1, 0)  # asumsi baris pertama = header
        if abs(new_body_rows - orig_body_rows) > 0:
            mismatches += 1
            issues["table_row_mismatch"].append({
                "table_index": i,
                "block_id": b["id"],
                "original_rows(approx_body)": orig_body_rows,
                "processed_rows(body)": new_body_rows,
            })

    # hitung berapa grup tabel markdown-pipe yang seharusnya terdeteksi di sumber,
    # dibandingkan jumlah yang benar2 masuk sbg block_type='table' hasil processing
    n_original_pipe_tables = 0
    kk = 0
    while kk < len(src_lines):
        line = src_lines[kk].strip()
        if (MD_TABLE_ROW_RE.match(line) and kk + 1 < len(src_lines)
                and MD_TABLE_SEP_RE.match(src_lines[kk + 1].strip())):
            n_original_pipe_tables += 1
            kk += 2
            while kk < len(src_lines) and MD_TABLE_ROW_RE.match(src_lines[kk].strip()):
                kk += 1
            continue
        kk += 1
    if n_original_pipe_tables != len(processed_pipe_tables):
        issues["table_row_mismatch"].append({
            "table_index": "markdown_pipe_group_count",
            "original_pipe_table_groups": n_original_pipe_tables,
            "processed_pipe_table_blocks": len(processed_pipe_tables),
        })
        mismatches += 1

    # --- 3) Block kosong / anomali ---
    for b in blocks:
        content = b["content"].strip()
        if not content:
            issues["empty_or_short_blocks"].append(b["id"])
        if b["block_type"] == "heading" and (len(content) > 120 or content.endswith((".", ",", ":"))):
            issues["suspicious_headings"].append({"id": b["id"], "content": content[:100]})

    # --- 4) Duplikasi ---
    content_counter = Counter(b["content"].strip() for b in blocks if b["content"].strip())
    for content, cnt in content_counter.items():
        if cnt > 1 and len(content) > 15:  # abaikan string pendek yang wajar berulang (mis. "Sumber: QS WUR, 2026")
            dup_ids = [b["id"] for b in blocks if b["content"].strip() == content]
            issues["duplicate_blocks"].append({"content": content[:80], "count": cnt, "ids": dup_ids})

    summary = {
        "source_file": md_path.name,
        "total_blocks": len(blocks),
        "block_type_counts": dict(Counter(b["block_type"] for b in blocks)),
        "original_word_count_cleaned": original_wc,
        "processed_word_count": processed_wc,
        "content_coverage_pct": coverage_pct,
        "content_coverage_nontable_pct": coverage_nontable_pct,
        "content_coverage_table_pct": coverage_table_pct,
        "original_table_count(html)": len(original_table_rows),
        "processed_table_count(html)": len(processed_html_tables),
        "processed_table_count(markdown_pipe)": len(processed_pipe_tables),
        "table_row_mismatches": mismatches,
        "empty_blocks": len(issues["empty_or_short_blocks"]),
        "suspicious_headings": len(issues["suspicious_headings"]),
        "duplicate_block_groups": len(issues["duplicate_blocks"]),
    }

    report = {"summary": summary, "issues": issues}
    out_path = REPORT_DIR / f"{md_path.stem}_validation.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary


def main():
    for md_file in sorted(PARSED_DIR.glob("*.md")):
        json_file = PROCESSED_DIR / f"{md_file.stem}_processed.json"
        if not json_file.exists():
            print(f"[SKIP] {json_file} tidak ditemukan")
            continue
        summary = validate_file(md_file, json_file)
        print(f"\n=== {summary['source_file']} ===")
        for k, v in summary.items():
            if k != "source_file":
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
