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

MAX_CONTENT_LEN = 1500  # ngưỡng tách sub-chunk

# =========================
# Regex & helpers
# =========================
CHAPTER_RE = re.compile(
    r"^\s*(?i:chương)\s+(?P<num>(?i:[ivxlcdm]+)|\d+)\s*[:\.]?\s*(?P<title>.*)$",
    flags=re.UNICODE,
)

ARTICLE_RE = re.compile(
    r"^\s*(?i:điều)\s+(?P<num>\d+)\.\s*(?P<title>.*)$",
    flags=re.UNICODE,
)

# "khoản" ở cấp điều: dòng bắt đầu bằng số-thứ-tự + dấu chấm + khoảng trắng
KHOAN_HEAD_RE = re.compile(r"(?m)^\s*\d+\.\s")

# marker kết thúc tài liệu chính
END_APPENDIX_LINE_RE = re.compile(r"^\s*\./\.\s*$")     # dòng chỉ có ./.
END_APPENDIX_ANY_RE  = re.compile(r"\./\.")             # ./. xuất hiện ở bất kỳ đâu trong dòng

# Tiêu đề điều/khoản nằm trong ngoặc kép (dòng dạng “Điều 3a. …” hoặc “1. …”)
QUOTED_TITLE_RE = re.compile(r"[“\"]\s*(?i:(Điều|khoản|\d+\.))\s+.*")

# =========================
# Utilities
# =========================
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
# Quote helpers
# =========================
def compute_quote_spans(text: str) -> List[Tuple[int, Optional[int], str]]:
    """
    Trả về danh sách span các vùng trích dẫn: (start, end_or_None, kind)
    kind ∈ {"curly","dbl"} tương ứng “ ” hoặc "
    - Nếu không tìm thấy dấu đóng, end = None (vùng mở tới hết).
    """
    spans: List[Tuple[int, Optional[int], str]] = []

    # Xử lý ngoặc cong “ … ”
    open_positions: List[int] = []
    for i, ch in enumerate(text):
        if ch == "“":
            open_positions.append(i)
        elif ch == "”":
            if open_positions:
                s = open_positions.pop()
                spans.append((s, i + 1, "curly"))
    # các “ còn mở
    for s in open_positions:
        spans.append((s, None, "curly"))

    # Xử lý ngoặc kép thẳng " … "
    # Duyệt ký tự và ghép theo cặp (bỏ qua bên trong cặp “ ” đã bắt? Không cần — chúng độc lập)
    dbl_positions: List[int] = []
    for i, ch in enumerate(text):
        if ch == '"':
            dbl_positions.append(i)
    # ghép theo cặp liên tiếp
    for j in range(0, len(dbl_positions), 2):
        s = dbl_positions[j]
        e = dbl_positions[j + 1] + 1 if j + 1 < len(dbl_positions) else None
        spans.append((s, e, "dbl"))

    # Sắp xếp theo start
    spans.sort(key=lambda x: x[0])
    return spans


def is_offset_in_any_span(offset: int, spans: List[Tuple[int, Optional[int], str]]) -> bool:
    for s, e, _ in spans:
        if e is None:
            if offset >= s:
                return True
        else:
            if s <= offset < e:
                return True
    return False


def first_quoted_title_line(text: str) -> Optional[str]:
    """
    Lấy dòng tiêu đề đầu tiên nằm trong ngoặc kép (vd: “Điều 3a. …” hoặc “1. …”).
    """
    m = QUOTED_TITLE_RE.search(text)
    if not m:
        return None
    # Lấy nguyên dòng chứa match
    s = text.rfind("\n", 0, m.start())
    e = text.find("\n", m.end())
    if e == -1:
        e = len(text)
    line = text[s + 1 : e].strip()
    return line


def leading_khoan_title(segment: str) -> Optional[str]:
    """
    Nếu segment bắt đầu bằng một dòng 'khoản' (n. <text>), trả về nguyên dòng đó như 'tiêu đề khoản'.
    """
    # Lấy dòng đầu tiên
    nl = segment.find("\n")
    first_line = segment if nl == -1 else segment[:nl]
    if re.match(r"^\s*\d+\.\s", first_line):
        return first_line.strip()
    return None


# =========================
# Splitting helpers
# =========================
def split_long_text_at_sentence(text: str, limit: int) -> List[str]:
    """Cắt text thành các phần <= limit, ưu tiên dừng ở dấu chấm hoặc newline gần nhất."""
    chunks: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        end = min(i + limit, n)
        if end == n:
            chunks.append(text[i:].rstrip())
            break

        period = text.rfind(".", i, end)
        if period != -1 and period > i:
            split = period + 1
        else:
            nl = text.rfind("\n", i, end)
            split = nl + 1 if nl != -1 and nl > i else end

        chunks.append(text[i:split].rstrip())
        i = split
    return chunks


def split_article_content(header_line: str, body_text: str, chapter: str) -> List[Dict[str, str]]:
    """
    Trả về 1 hoặc nhiều chunk cho 1 điều.
    - Nếu > MAX_CONTENT_LEN: chia nhỏ theo khoản rồi tiếp tục cắt theo câu.
    - Khi cắt một block khoản thành nhiều phần:
        + Luôn prepend header Điều (đã có).
        + Với các phần sau (idx>0), prepend thêm 'tiêu đề khoản' nếu có.
        + Nếu phần đó nằm bên trong vùng trích dẫn bị chia đôi, prepend thêm 'tiêu đề trong ngoặc kép' đầu tiên của block.
    """
    content_full = header_line + ("\n" + body_text if body_text else "")
    if len(content_full) <= MAX_CONTENT_LEN:
        return [{"content": content_full, "chapter": chapter or "", "article": header_line}]

    # 1) Chia body theo các block khoản
    matches = list(KHOAN_HEAD_RE.finditer(body_text))
    blocks: List[Tuple[int, int]] = []
    if not matches:
        blocks.append((0, len(body_text)))
    else:
        starts = [m.start() for m in matches]
        if starts[0] > 0:
            blocks.append((0, starts[0]))  # phần mở đầu trước khoản đầu
        for idx, s in enumerate(starts):
            e = starts[idx + 1] if idx + 1 < len(starts) else len(body_text)
            blocks.append((s, e))

    out_chunks: List[Dict[str, str]] = []
    buf = ""  # buffer để gộp nhiều segment nếu còn trong giới hạn

    def flush_buf():
        nonlocal buf
        if not buf.strip():
            return
        out_chunks.append({
            "content": header_line + "\n" + buf.strip(),
            "chapter": chapter or "",
            "article": header_line,
        })
        buf = ""

    # 2) Xử lý từng block khoản
    for (s, e) in blocks:
        segment = body_text[s:e].strip("\n")

        # Nếu cả header + segment <= limit, thử gom vào buf
        if len(header_line) + 1 + len(segment) <= MAX_CONTENT_LEN:
            candidate = buf + ("\n" if buf else "") + segment
            if len(header_line) + 1 + len(candidate) <= MAX_CONTENT_LEN:
                buf = candidate
                continue
            else:
                flush_buf()
                buf = segment
                continue

        # 3) Segment quá dài -> phải cắt theo câu
        #    Chuẩn bị bối cảnh cho tất cả phần cắt trong segment này
        flush_buf()
        khoan_title = leading_khoan_title(segment)  # vd: "7. Sửa đổi khoản 1 Điều 10 như sau:"
        spans = compute_quote_spans(segment)        # vùng “ … ” hoặc " … "
        quoted_title = first_quoted_title_line(segment)  # vd: “1. Cục Hóa chất là ...

        # Cắt segment thành các parts
        parts = split_long_text_at_sentence(segment, MAX_CONTENT_LEN - len(header_line) - 1)

        # Để xác định offset bắt đầu của mỗi part trong segment
        offsets: List[int] = []
        base = 0
        for p in parts:
            offsets.append(base)
            base += len(p)

        for idx, part in enumerate(parts):
            start_off = offsets[idx]
            # part này có đang bắt đầu bên trong một vùng ngoặc kép?
            in_quote_here = is_offset_in_any_span(start_off, spans)

            # Xây sub-content
            prefix_lines: List[str] = [header_line]
            # Với các phần sau (idx>0), bổ sung tiêu đề khoản (nếu block này là khoản)
            if idx > 0 and khoan_title:
                prefix_lines.append(khoan_title)
            # Nếu phần bắt đầu bên trong vùng trích dẫn, thêm tiêu đề trích dẫn đầu tiên
            if in_quote_here and quoted_title:
                # Tránh duplicate nếu quoted_title đã chính là dòng đầu của part
                first_line = part.split("\n", 1)[0].strip()
                if quoted_title.strip() != first_line:
                    prefix_lines.append(quoted_title)

            sub_content = "\n".join(prefix_lines) + "\n" + part.strip()
            out_chunks.append({
                "content": sub_content,
                "chapter": chapter or "",
                "article": header_line,
            })

    # Flush phần còn lại nếu có
    if buf:
        flush_buf()

    return out_chunks


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
    end_reached = False

    def flush_article():
        if current_article_full is None:
            return
        header_line = current_article_full
        body_text = "\n".join(current_body).strip()
        sub_chunks = split_article_content(header_line, body_text, current_chapter or "")
        chunks.extend(sub_chunks)

    i = 0
    n = len(lines)
    while i < n:
        if end_reached:
            break

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
            if END_APPENDIX_LINE_RE.match(line):
                flush_article()
                end_reached = True
                break

            m = END_APPENDIX_ANY_RE.search(line)
            if m:
                before = line[:m.start()]
                if before.strip():
                    current_body.append(before.rstrip())
                flush_article()
                end_reached = True
                break

            current_body.append(line)

        i += 1

    if not end_reached:
        flush_article()

    return chunks


# =========================
# Metadata & CSV output
# =========================
def load_user_metadata(json_path: Optional[Path]) -> Dict[str, str]:
    if not json_path:
        return {}
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("File JSON metadata phải là một object (key/value).")
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def write_csv(rows: List[Dict[str, str]], user_meta: Dict[str, str], out_path: Path) -> None:
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
# CLI entry
# =========================
def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Load DOCX → chunk theo Điều → xuất CSV (kèm metadata Chương/Điều + JSON metadata)."
    )
    parser.add_argument("--docx", required=True, help="Đường dẫn file .docx (bắt buộc)")
    parser.add_argument("--meta", required=False, help="Đường dẫn file .json metadata (tùy chọn)")
    parser.add_argument("--out", required=False, help="Đường dẫn file .csv đầu ra (mặc định: <tên_docx>_chunks.csv)")
    args = parser.parse_args()

    docx_path = Path(args.docx)
    if docx_path.suffix.lower() != ".docx":
        raise SystemExit("Chỉ chấp nhận file DOCX (.docx).")

    json_path = Path(args.meta) if args.meta else None
    out_path = Path(args.out) if args.out else docx_path.with_name(docx_path.stem + "_chunks.csv")

    lines = read_docx_lines(docx_path)
    if not lines:
        raise SystemExit("Không đọc được nội dung từ DOCX (file rỗng?).")

    chunks = chunk_by_articles(lines)
    if not chunks:
        raise SystemExit("Không tìm thấy bất kỳ 'Điều {số}.' nào ở đầu dòng. Kiểm tra lại cấu trúc file DOCX.")

    user_meta = load_user_metadata(json_path)
    write_csv(chunks, user_meta, out_path)
    logger.info(f"Exported CSV: {out_path}")


if __name__ == "__main__":
    main()
