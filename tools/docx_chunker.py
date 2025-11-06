from __future__ import annotations
import argparse
import csv
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docx import Document as DocxDocument


logger = logging.getLogger(__name__)


# =========================
# Regex & helpers
# =========================
CHAPTER_RE = re.compile(
    r"^\s*(?i:chương)\s+(?P<num>[IVXLCDM]+|\d+)\s*\.?\s*(?P<title>.*)$",
    flags=re.UNICODE,
)

ARTICLE_RE = re.compile(
    r"^\s*(?i:điều)\s+(?P<num>\d+)\.\s*(?P<title>.*)$",
    flags=re.UNICODE,
)


def clean_line(s: str) -> str:
    return s.replace("\u00A0", " ").strip()


def parse_chapter_inline(line: str) -> Optional[Tuple[str, str, str]]:
    m = CHAPTER_RE.match(line)
    if not m:
        return None
    num = m.group("num").strip()
    inline_title = (m.group("title") or "").strip()
    return line, num, inline_title


def is_article_header(line: str) -> Optional[Tuple[str, str, str]]:
    m = ARTICLE_RE.match(line)
    if not m:
        return None
    num = m.group("num").strip()
    title = (m.group("title") or "").strip()
    article_full = f"Điều {num}." + (f" {title}" if title else "")
    return article_full, num, title


# =========================
# Core logic
# =========================
def read_docx_lines(docx_path: Path) -> List[str]:
    doc = DocxDocument(str(docx_path))
    out: List[str] = []
    for p in doc.paragraphs:
        t = clean_line(p.text or "")
        if t:
            out.append(t)
    return out


def build_chapter_name(line: str, num: str, inline_title: str, next_line: Optional[str]) -> str:
    def looks_like_chapter(s: str) -> bool:
        return bool(CHAPTER_RE.match(s))

    def looks_like_article(s: str) -> bool:
        return bool(ARTICLE_RE.match(s))

    if inline_title:
        return f"Chương {num}. {inline_title}"

    if next_line and not looks_like_chapter(next_line) and not looks_like_article(next_line):
        return f"Chương {num}. {next_line}"

    return f"Chương {num}"


def chunk_by_articles(lines: List[str]) -> List[Dict[str, str]]:
    current_chapter: Optional[str] = None
    current_article_full: Optional[str] = None
    current_body: List[str] = []

    chunks: List[Dict[str, str]] = []

    def flush_article():
        if current_article_full is None:
            return
        header_line = current_article_full
        body_text = "\n".join(current_body).strip()
        content = header_line + ("\n" + body_text if body_text else "")

        chunks.append(
            {
                "content": content,
                "chapter": current_chapter or "",
                "article": header_line,
            }
        )

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        chap = parse_chapter_inline(line)
        if chap:
            flush_article()
            current_article_full = None
            current_body = []

            chapter_line, num, inline_title = chap
            next_line = lines[i + 1] if (i + 1) < n else None
            current_chapter = build_chapter_name(chapter_line, num, inline_title, next_line)
            i += 1
            continue

        art = is_article_header(line)
        if art:
            flush_article()
            current_body = []
            current_article_full, _, _ = art
            i += 1
            continue

        if current_article_full:
            current_body.append(line)

        i += 1

    flush_article()
    return chunks


def load_user_metadata(json_path: Optional[Path]) -> Dict[str, str]:
    if not json_path:
        return {}
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("File JSON metadata phải là một object (key/value).")
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def write_csv(
    rows: List[Dict[str, str]],
    user_meta: Dict[str, str],
    out_path: Path,
) -> None:
    base_cols = ["content", "content_length", "chapter", "article"]
    extra_cols = list(user_meta.keys())
    fieldnames = base_cols + extra_cols

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in rows:
            row = dict(r)
            row["content_length"] = str(len(row.get("content", "")))
            for k, v in user_meta.items():
                row.setdefault(k, v)
            writer.writerow(row)


# =========================
# CLI
# =========================
def main():
    # Configure basic logging for CLI usage
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Load DOCX → chunk theo Điều → xuất CSV (kèm metadata Chương/Điều + JSON metadata)."
    )
    parser.add_argument("--docx", required=True, help="Đường dẫn file .docx (bắt buộc)")
    parser.add_argument("--meta", required=False, help="Đường dẫn file .json metadata (tùy chọn)")
    parser.add_argument(
        "--out",
        required=False,
        help="Đường dẫn file .csv đầu ra (mặc định: <tên_docx>_chunks.csv cùng thư mục)",
    )
    args = parser.parse_args()

    docx_path = Path(args.docx)
    if docx_path.suffix.lower() != ".docx":
        raise SystemExit("Chỉ chấp nhận file DOCX (.docx).")

    json_path = Path(args.meta) if args.meta else None
    out_path = (
        Path(args.out)
        if args.out
        else docx_path.with_name(docx_path.stem + "_chunks.csv")
    )

    lines = read_docx_lines(docx_path)
    if not lines:
        raise SystemExit("Không đọc được nội dung từ DOCX (file rỗng?).")

    chunks = chunk_by_articles(lines)
    if not chunks:
        raise SystemExit(
            "Không tìm thấy bất kỳ 'Điều {số}.' nào ở đầu dòng. Kiểm tra lại cấu trúc file DOCX."
        )

    user_meta = load_user_metadata(json_path)

    write_csv(chunks, user_meta, out_path)

    logger.info(f"Exported CSV: {out_path}")


if __name__ == "__main__":
    main()
