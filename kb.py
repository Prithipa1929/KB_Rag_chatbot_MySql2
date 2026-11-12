"""Knowledge base: chunks stored in MySQL, searched with TF-IDF (no API key needed)."""
import difflib
import re
import threading
from collections import Counter
from pathlib import Path

from pypdf import PdfReader
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

import config
from db import get_db_connection

SUPPORTED = (".pdf", ".txt", ".md")
_lock = threading.Lock()
_index = {"sig": None, "vec": None, "matrix": None, "rows": None, "vocab": [], "stems": set()}
_PAGE_NO = re.compile(r"^(page\s*)?\d{1,4}(\s*(/|of)\s*\d{1,4})?$", re.I)


def init_kb():
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    source_file VARCHAR(255) NOT NULL,
                    page_number INT NOT NULL,
                    text TEXT NOT NULL,
                    INDEX idx_chunks_file (source_file)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
            )
        conn.commit()
    finally:
        conn.close()


def _strip_headers_footers(pages):
    norm = lambda l: re.sub(r"\d+", "#", re.sub(r"\s+", " ", l.strip().lower()))
    counts = Counter()
    for _, t in pages:
        for k in {norm(x) for x in t.splitlines() if x.strip()}:
            counts[k] += 1
    thr = max(3, int(len(pages) * 0.4)) if len(pages) >= 4 else 10**9
    seen, out = set(), []
    for no, t in pages:
        keep = []
        for line in t.splitlines():
            s = line.strip()
            if s and _PAGE_NO.match(s):
                continue
            k = norm(line)
            if s and counts[k] >= thr:
                if k in seen:
                    continue
                seen.add(k)
                keep.append(line)
                keep.append("")
                continue
            keep.append(line)
        out.append((no, "\n".join(keep)))
    return out


def _read_pages(path: Path):
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return _strip_headers_footers([(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)])
    return [(0, path.read_text(encoding="utf-8", errors="ignore"))]


def _is_heading(line: str) -> bool:
    core = re.sub(r"^\d+(\.\d+)*\.?\s+", "", line).rstrip(":").strip()
    words = core.split()
    return (
        1 <= len(words) <= 8
        and len(core) <= 70
        and not re.search(r"[.,;:!?\d]", core)
        and re.match(r"[A-Z]", core) is not None
    )


def _sections(text: str, heading: str = ""):
    sections, buf, prev_end = [], [], True
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            prev_end = True
            continue                
        if prev_end and _is_heading(line):
            if buf:
                sections.append((heading, " ".join(buf)))
                buf = []
            heading = re.sub(r"^\d+(\.\d+)*\.?\s+", "", line).rstrip(":").strip()
            continue
        buf.append(line)
        prev_end = bool(re.search(r"[.!?:]$", line))
    if buf:
        sections.append((heading, " ".join(buf)))
    return sections, heading


def _chunk(text: str):
    text = re.sub(r"\s+", " ", text).strip()
    size, overlap, n = config.CHUNK_SIZE, config.CHUNK_OVERLAP, len(text)
    out, start = [], 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            cut = max(text.rfind(". ", start, end) + 1, text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
        s = text.find(". ", start, end)
        if s != -1:
            start = s + 2
        else:
            sp = text.find(" ", start, end)
            start = sp + 1 if sp != -1 else end
    return out


def ingest_file(path: Path) -> int:
    rows, heading = [], ""
    for page_no, text in _read_pages(path):
        sections, heading = _sections(text, heading)
        for head, para in sections:
            for piece in _chunk(para):
                rows.append((path.name, page_no, f"## {head}\n{piece}" if head else piece))
    if not rows:
        raise ValueError("No readable text found (is it a scanned PDF?)")

    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM chunks WHERE source_file = %s", (path.name,))
            c.executemany("INSERT INTO chunks (source_file, page_number, text) VALUES (%s, %s, %s)", rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def ingest_all() -> dict:
    files = sorted(p for p in config.DOCS_DIR.iterdir() if p.suffix.lower() in SUPPORTED)
    return {p.name: ingest_file(p) for p in files}


def delete_document(name: str) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM chunks WHERE source_file = %s", (name,))
        conn.commit()
    finally:
        conn.close()
    (config.DOCS_DIR / name).unlink(missing_ok=True)


def list_documents() -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT source_file, COUNT(*) AS n FROM chunks GROUP BY source_file")
            rows = c.fetchall()
            return {r["source_file"]: r["n"] for r in rows}
    finally:
        conn.close()


def _stem(w: str) -> str:
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def _tokens(text: str):
    return [
        _stem(w)
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in ENGLISH_STOP_WORDS and (len(w) > 1 or w.isdigit())
    ]


def _plain(text: str) -> str:
    return text[3:] if text.startswith("## ") else text


def _state():
    with _lock:
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m FROM chunks")
                r = c.fetchone()
                sig = (r["n"], r["m"])
                if sig != _index["sig"]:
                    c.execute("SELECT source_file, page_number, text FROM chunks")
                    rows = c.fetchall()
                    if rows:
                        vec = TfidfVectorizer(
                            tokenizer=_tokens, token_pattern=None, ngram_range=(1, 2), sublinear_tf=True
                        )
                        texts = [_plain(x["text"]) for x in rows]
                        matrix = vec.fit_transform(texts)
                        vocab = sorted({w for t in texts for w in re.findall(r"[a-z]{4,}", t.lower())})
                        _index.update(
                            sig=sig,
                            vec=vec,
                            matrix=matrix,
                            rows=rows,
                            vocab=vocab,
                            stems={_stem(w) for w in vocab},
                        )
                    else:
                        _index.update(sig=sig, vec=None, matrix=None, rows=[], vocab=[], stems=set())
        finally:
            conn.close()
        return dict(_index)


def correct_query(query: str) -> str:
    st = _state()
    if st["vec"] is None:
        return query
    vocab, stems, out = set(st["vocab"]), st["stems"], []
    for w in query.split():
        core = re.sub(r"[^a-z]", "", w.lower())
        if len(core) >= 4 and core not in vocab and _stem(core) not in stems and core not in ENGLISH_STOP_WORDS:
            m = difflib.get_close_matches(core, st["vocab"], n=1, cutoff=0.8)
            out.append(m[0] if m else w)
        else:
            out.append(w)
    return " ".join(out)


def search(query: str, k: int = None):
    """Top chunks for a question: [{source_file, page_number, text, score}]."""
    k = k or config.TOP_K
    st = _state()
    if st["vec"] is None:
        return []

    query_vec = st["vec"].transform([query])
    scores = linear_kernel(query_vec, st["matrix"]).ravel()
    best = scores.argsort()[::-1]

    results = []
    for i in best[:k]:
        if scores[i] >= config.MIN_SCORE:
            results.append({
                "source_file": st["rows"][i]["source_file"],
                "page_number": st["rows"][i]["page_number"],
                "text": st["rows"][i]["text"],
                "score": float(scores[i]),
            })

    if not results:
        for i in best[:k]:
            if scores[i] > 0.0:
                results.append({
                    "source_file": st["rows"][i]["source_file"],
                    "page_number": st["rows"][i]["page_number"],
                    "text": st["rows"][i]["text"],
                    "score": float(scores[i]),
                })

    return results


def score_texts(query: str, texts):
    st = _state()
    if st["vec"] is None or not texts:
        return [0.0] * len(texts)
    return linear_kernel(st["vec"].transform([query]), st["vec"].transform(texts)).ravel().tolist()


SYNONYMS = {
    "founder": ["founders", "cofounder", "founded", "established", "started", "owner", "ceo", "director"],
    "leave": ["vacation", "holiday", "timeoff", "absence"],
    "salary": ["pay", "payroll", "compensation", "wage"],
    "wfh": ["remote", "work from home", "hybrid"],
    "name": ["company", "pvt", "ltd", "overview", "about", "handbook"],
}

_GENERIC = {"company", "organization", "organisation", "firm"}


def expand_query(query: str) -> str:
    st = _state()
    vocab = set(st["vocab"])
    toks = set(re.findall(r"[a-z]+", query.lower()))
    extra = []
    for key, syns in SYNONYMS.items():
        if key in toks or toks & set(syns):
            for term in [key] + syns:
                if not vocab or any(w in vocab for w in term.split()):
                    extra.append(term)
    return (query + " " + " ".join(dict.fromkeys(extra))).strip()


def keyword_search(query: str, k: int = 8):
    """Fallback: LIKE search on chunks (supports short acronyms like HR, PF, WFH and numbers)."""
    words = [w for w in re.findall(r"[a-z0-9]{2,}", query.lower()) if w not in ENGLISH_STOP_WORDS]
    words = list(dict.fromkeys(words))[:12]
    if not words:
        return []
    cond = " OR ".join(["LOWER(text) LIKE %s"] * len(words))
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(f"SELECT source_file, page_number, text FROM chunks WHERE {cond}",
                      [f"%{w}%" for w in words])
            rows=list(c.fetchall())
    finally:
        conn.close()
    for r in rows:
        low = r["text"].lower()
        r["score"] = sum(1 for w in words if w in low) / len(words)
    rows.sort(key=lambda r: -r["score"])
    return rows[:k]