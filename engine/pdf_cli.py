"""Text-only PDF reader exported from the maintained DocKit implementation."""
import re
import time
from pathlib import Path
from typing import Optional
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

def parse_page_spec(spec: Optional[str], total_pages: int) -> list[int]:
    """Parse "1-5,8,10-12" → sorted 0-indexed page list.

    spec=None → all pages.
    Validates against total_pages; clamps to [1, total_pages]; raises on invalid syntax.
    """
    if not spec:
        return list(range(total_pages))
    out: set[int] = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"^(\d+)(?:-(\d+))?$", tok)
        if not m:
            raise ValueError(f"invalid page spec token: {tok!r}")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < 1 or a > b:
            raise ValueError(f"invalid page range: {tok!r}")
        for p in range(a, b + 1):
            if 1 <= p <= total_pages:
                out.add(p - 1)
    return sorted(out)

def _text_extract(pdf, args, out_dir: Path) -> dict:
    """pdf-based: pdfplumber extract_text() per page to .txt files.

    args.text_extract_pages — page spec (e.g. "1-5"), None=all
    args.text_extract_single — bool, concat all into full.txt
    """
    if pdf is None:
        return {"step": "text-extract",
                "error": "pdfplumber not available or pdf is None"}
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = (getattr(args, "text_extract_pages", None)
            or getattr(args, "pages", None))
    single = bool(getattr(args, "text_extract_single", False)
                  or getattr(args, "single", False))
    total = len(pdf.pages)
    page_idx = parse_page_spec(spec, total)
    pad = max(3, len(str(total)))
    pages_written: list[str] = []
    chunks: list[str] = []
    for i in page_idx:
        try:
            txt = pdf.pages[i].extract_text() or ""
        except Exception as e:
            txt = f"[ERROR extract_text page {i+1}: {type(e).__name__}: {e}]"
        if single:
            chunks.append(f"\n\n===== page {i+1} =====\n\n{txt}")
        else:
            fn = out_dir / f"page-{str(i+1).zfill(pad)}.txt"
            fn.write_text(txt, encoding="utf-8")
            pages_written.append(fn.name)
    if single:
        full = out_dir / "full.txt"
        full.write_text("".join(chunks), encoding="utf-8")
        pages_written = ["full.txt"]
    return {
        "step": "text-extract",
        "pages_requested": len(page_idx),
        "files_written": len(pages_written),
        "out_dir": str(out_dir),
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "mode": "single" if single else "per-page",
    }
