"""Read-only HTML bucket reader; no personal index, share service or publisher."""
import html as html_lib
import re
from pathlib import Path
from datetime import datetime
PROJECT_EXCLUDE_DIRS = {"node_modules", ".next", "dist", "build", ".git", ".venv", "__pycache__", ".turbo", ".cache", "vendor", "site-packages"}
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_EXCLUDE_RE = re.compile(r"_archive|\.bak", re.I)

def _clean_title(raw: str) -> str:
    """Strip inner tags, unescape entities, collapse whitespace."""
    txt = _TAG_RE.sub("", raw)
    txt = html_lib.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def extract_title(path: Path, fallback: str) -> str:
    """<title> → first <h1> → filename stem. Read head bytes only (cheap)."""
    try:
        # titles/h1 are near the top; reading the whole file is fine for HTML
        # reports (tens of KB) but cap to be safe against any rogue large file.
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fallback
    m = _TITLE_RE.search(text)
    if m:
        t = _clean_title(m.group(1))
        if t:
            return t
    m = _H1_RE.search(text)
    if m:
        t = _clean_title(m.group(1))
        if t:
            return t
    return fallback

def scan_bucket(bucket: dict, *, check_share: bool) -> list[dict]:
    """Read-only scan one bucket → list of entry dicts (mtime-desc sorted)."""
    d: Path = bucket["dir"]
    entries: list[dict] = []
    if not d.is_dir():
        return entries
    for p in d.glob(bucket["glob"]):
        if not p.is_file():
            continue
        rp = str(p)
        if _EXCLUDE_RE.search(rp):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        title = extract_title(p, fallback=p.stem)
        mtime = datetime.fromtimestamp(st.st_mtime).astimezone()
        entry = {
            "bucket": bucket["key"],
            "path": p,
            "basename": p.name,
            "title": title,
            "mtime": mtime,
            "mtime_iso": mtime.isoformat(timespec="seconds"),
            "size": st.st_size,
            "file_url": p.resolve().as_uri(),  # file:///abs/path
            "share_url": None,
            "share_status": None,
        }
        entries.append(entry)
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    # collapse daily series (e.g. scratch-distill-YYYY-MM-DD): keep only newest
    collapse = bucket.get("collapse_series")
    if collapse:
        seen: set[str] = set()
        kept: list[dict] = []
        for e in entries:  # already mtime-desc → first seen is newest
            pref = next((c for c in collapse if e["basename"].startswith(c)), None)
            if pref is not None:
                if pref in seen:
                    continue
                seen.add(pref)
            kept.append(e)
        entries = kept
    return entries
