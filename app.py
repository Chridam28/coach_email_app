import streamlit as st
import csv
import io
import re
import time
from dataclasses import dataclass
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------- STREAMLIT UI SETUP ----------------------------

st.set_page_config(page_title="Coach Email Extractor", layout="wide")
st.title("Coach Email Extractor — Web (same logic as Windows app)")

# ---------------------------- SCRAPER LOGIC (IDENTICAL) ----------------------------

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

OBFUSCATIONS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\s*\[at\]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*\(at\)\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s+at\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s*\[dot\]\s*", re.IGNORECASE), "."),
    (re.compile(r"\s*\(dot\)\s*", re.IGNORECASE), "."),
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
]

# Ruoli target: coaching + recruiting
TARGET_ROLE_KEYWORDS = [
    # coaching
    "head coach",
    "assistant coach",
    "asst coach",
    "associate head coach",
    "associate coach",
    "interim head coach",
    "coach",

    # recruiting (filtrato per sport quando arriva dalla staff directory generale)
    "recruiting",
    "recruiting coordinator",
    "recruiting coord",
    "director of recruiting",
    "recruiting director",
    "recruiting operations",
    "recruiting ops",
    "coordinator of recruiting",
]

# Escludi student / graduate assistant
EXCLUDE_ROLE_KEYWORDS = [
    "student assistant",
    "student asst",
    "student-athlete assistant",
    "graduate assistant",
    "grad assistant",
    "grad asst",
]

EXCLUDE_ABBREV_PATTERNS = [
    re.compile(r"\bga\b", re.IGNORECASE),
    re.compile(r"\bg\.a\.\b", re.IGNORECASE),
    re.compile(r"\bsa\b", re.IGNORECASE),
    re.compile(r"\bs\.a\.\b", re.IGNORECASE),
]

@dataclass
class Target:
    university: str
    sport: str
    url: str
    staff_directory_url: str = ""

def deobfuscate(text: str) -> str:
    t = text
    for pat, repl in OBFUSCATIONS:
        t = pat.sub(repl, t)
    return t

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())

def make_session() -> requests.Session:
    s = requests.Session()
    # Header “da browser” per ridurre blocchi/405
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })
    return s

def fetch(session: requests.Session, url: str, timeout: int = 10) -> str:
    r = session.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text

def is_same_domain(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except Exception:
        return False

def extract_emails_anywhere(soup: BeautifulSoup) -> Set[str]:
    """
    Estrae email da mailto:, testo e attributi (con deobfuscate).
    """
    emails: Set[str] = set()

    for a in soup.select('a[href^="mailto:"]'):
        href = a.get("href", "")
        e = href.split("mailto:", 1)[1].split("?", 1)[0].strip()
        if e:
            emails.add(e)

    text = deobfuscate(soup.get_text(" ", strip=True))
    emails.update(EMAIL_RE.findall(text))

    for tag in soup.find_all(True):
        for _, val in tag.attrs.items():
            if isinstance(val, str):
                vv = deobfuscate(val)
                emails.update(EMAIL_RE.findall(vv))

    return {e.strip() for e in emails if e.strip()}

def find_candidate_blocks(soup: BeautifulSoup) -> List:
    rows = soup.select("table tr")
    if len(rows) >= 5:
        return rows

    selectors = [
        ".sidearm-staff-directory__item",
        ".sidearm-roster-coach",
        ".staff-member",
        ".coaches-item",
        ".coach",
        ".bio",
        "article",
        "li",
        "div",
    ]

    for sel in selectors:
        els = soup.select(sel)
        if 5 <= len(els) <= 400:
            return els

    return [soup.body] if soup.body else [soup]

def block_text(el) -> str:
    return norm(deobfuscate(el.get_text(" ", strip=True)))

def is_excluded_block(text: str) -> bool:
    for k in EXCLUDE_ROLE_KEYWORDS:
        if k in text:
            return True
    for pat in EXCLUDE_ABBREV_PATTERNS:
        if pat.search(text):
            return True
    return False

def is_target_block(text: str) -> bool:
    if is_excluded_block(text):
        return False
    return any(k in text for k in TARGET_ROLE_KEYWORDS)

def emails_in_block(el) -> Set[str]:
    emails: Set[str] = set()

    for a in el.select('a[href^="mailto:"]'):
        href = a.get("href", "")
        e = href.split("mailto:", 1)[1].split("?", 1)[0].strip()
        if e:
            emails.add(e)

    txt = deobfuscate(el.get_text(" ", strip=True))
    emails.update(EMAIL_RE.findall(txt))
    return {e.strip() for e in emails if e.strip()}

# ---------- SPORT FILTER (per staff directory generale) ----------

def sport_tokens(sport: str) -> Set[str]:
    s = sport.strip().lower()
    s_clean = re.sub(r"[^a-z0-9\s']", " ", s)
    s_clean = re.sub(r"\s+", " ", s_clean).strip()

    tokens: Set[str] = set()
    if s_clean:
        tokens.add(s_clean)
        tokens.add(s_clean.replace("’", "'"))
        tokens.add(s_clean.replace("'", ""))  # mens basketball

    words = [w for w in re.split(r"\s+", s_clean) if w]
    for w in words:
        if len(w) >= 4:
            tokens.add(w)

    if "basketball" in s_clean:
        tokens.add("basketball")
        if "men" in s_clean:
            tokens.add("mbkb")
            tokens.add("m basketball")
            tokens.add("mens basketball")
        if "women" in s_clean:
            tokens.add("wbkb")
            tokens.add("w basketball")
            tokens.add("womens basketball")

    if "soccer" in s_clean:
        tokens.add("soccer")
        if "men" in s_clean:
            tokens.add("msoc")
            tokens.add("mens soccer")
        if "women" in s_clean:
            tokens.add("wsoc")
            tokens.add("womens soccer")

    return {t for t in tokens if t}

def sport_match(text: str, sport: str) -> bool:
    tks = sport_tokens(sport)
    if not tks:
        return True
    return any(tok in text for tok in tks)

def collect_bio_links_from_target_blocks(
    soup: BeautifulSoup,
    base_url: str,
    sport: str,
    require_sport_match: bool
) -> List[str]:
    blocks = find_candidate_blocks(soup)
    links: Set[str] = set()

    for b in blocks:
        bt = block_text(b)
        if not is_target_block(bt):
            continue

        if require_sport_match and not sport_match(bt, sport):
            continue

        for a in b.select("a[href]"):
            href = a.get("href", "").strip()
            if not href:
                continue
            if href.startswith("mailto:") or href.startswith("tel:"):
                continue

            abs_url = urljoin(base_url, href)
            if not is_same_domain(abs_url, base_url):
                continue

            p = urlparse(abs_url).path.lower()
            if any(x in p for x in ["/staff", "/coaches", "/coach", "/people", "/person", "/bio", "/roster"]):
                links.add(abs_url)

    return sorted(links)

def extract_target_emails_from_page(html: str, base_url: str, sport: str, require_sport_match: bool) -> Set[str]:
    soup = BeautifulSoup(html, "lxml")
    blocks = find_candidate_blocks(soup)
    out: Set[str] = set()

    for b in blocks:
        bt = block_text(b)
        if not is_target_block(bt):
            continue
        if require_sport_match and not sport_match(bt, sport):
            continue
        out.update(emails_in_block(b))

    return out

def extract_from_bios(
    session: requests.Session,
    base_url: str,
    html: str,
    sport: str,
    require_sport_match: bool,
    max_bios: int = 30,
    sleep_s: float = 0.6
) -> Set[str]:
    soup = BeautifulSoup(html, "lxml")
    bio_links = collect_bio_links_from_target_blocks(
        soup=soup,
        base_url=base_url,
        sport=sport,
        require_sport_match=require_sport_match
    )[:max_bios]

    emails: Set[str] = set()
    for u in bio_links:
        try:
            bio_html = fetch(session, u)
            bio_soup = BeautifulSoup(bio_html, "lxml")

            bio_text = norm(deobfuscate(bio_soup.get_text(" ", strip=True)))
            if is_excluded_block(bio_text):
                continue

            emails.update(extract_emails_anywhere(bio_soup))
        except Exception:
            pass
        time.sleep(sleep_s)

    return emails

def join_emails(emails: Set[str]) -> str:
    return ", ".join(sorted(emails, key=lambda x: x.lower()))

def process_one_target(session: requests.Session, t: Target, sleep_s: float = 1.2) -> Set[str]:
    emails: Set[str] = set()

    # 1) pagina coaches dello sport (sport match non necessario)
    html = fetch(session, t.url)
    emails.update(extract_target_emails_from_page(html, t.url, t.sport, require_sport_match=False))

    # 2) fallback bio dalla pagina sport
    if not emails:
        emails.update(extract_from_bios(session, t.url, html, t.sport, require_sport_match=False))

    # 3) staff directory generale (QUI filtro sport)
    if not emails and t.staff_directory_url.strip():
        sdu = t.staff_directory_url.strip()
        sd_html = fetch(session, sdu)

        emails.update(extract_target_emails_from_page(sd_html, sdu, t.sport, require_sport_match=True))

        if not emails:
            emails.update(extract_from_bios(session, sdu, sd_html, t.sport, require_sport_match=True))

    time.sleep(sleep_s)
    return emails

def run_from_csv_bytes(csv_bytes: bytes, sleep_s: float, max_rows: int) -> Tuple[str, List[str]]:
    """
    Input: bytes of CSV with columns university,sport,url,staff_directory_url (optional).
    Output: CSV string (delimiter ';') and log lines.
    """
    logs: List[str] = []

    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    needed = {"university", "sport", "url"}
    if not reader.fieldnames or not needed.issubset(set(reader.fieldnames)):
        raise ValueError("Il CSV deve contenere: university, sport, url (e opzionale staff_directory_url)")

    targets: List[Target] = []
    for row in reader:
        u = (row.get("university") or "").strip()
        s = (row.get("sport") or "").strip()
        url = (row.get("url") or "").strip()
        sd = (row.get("staff_directory_url") or "").strip()
        if not (u and s and url):
            continue
        targets.append(Target(university=u, sport=s, url=url, staff_directory_url=sd))

    if not targets:
        raise ValueError("Nessuna riga valida trovata nel CSV.")

    if max_rows > 0:
        targets = targets[:max_rows]

    session = make_session()

    out_buf = io.StringIO()
    writer = csv.DictWriter(out_buf, fieldnames=["university", "emails"], delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    total = len(targets)
    for idx, t in enumerate(targets, start=1):
        logs.append(f"[{idx}/{total}] {t.university} — {t.sport}")
        try:
            emails = process_one_target(session, t, sleep_s=sleep_s)
            writer.writerow({"university": t.university, "emails": join_emails(emails)})
            logs.append(f"    -> {len(emails)} email(s)")
        except Exception as ex:
            writer.writerow({"university": t.university, "emails": f"ERROR: {ex}"})
            logs.append(f"    ERROR: {ex}")

    return out_buf.getvalue(), logs

# ---------------------------- STREAMLIT CONTROLS ----------------------------

col1, col2, col3 = st.columns([2, 1, 1])

with col1:
    uploaded = st.file_uploader("Carica input CSV (university,sport,url,staff_directory_url)", type=["csv"])

with col2:
    sleep_s = st.number_input("Pausa tra università (s)", min_value=0.0, max_value=10.0, value=1.2, step=0.2)

with col3:
    max_rows = st.number_input("Limite righe (0 = nessun limite)", min_value=0, max_value=50000, value=0, step=10)

run_btn = st.button("Esegui estrazione", type="primary", disabled=(uploaded is None))

if run_btn and uploaded is not None:
    try:
        data = uploaded.getvalue()
        progress = st.progress(0)
        status = st.empty()

        # Esegui e mostra log progressivo “simulato”
        # (il motore lavora in serie; progress aggiornato dai log dopo l'esecuzione completa sarebbe meno utile)
        status.write("Esecuzione in corso…")
        output_csv, logs = run_from_csv_bytes(data, float(sleep_s), int(max_rows))

        status.write("Completato ✅")
        st.download_button(
            label="Scarica output.csv",
            data=output_csv.encode("utf-8"),
            file_name="output.csv",
            mime="text/csv",
        )

        with st.expander("Log"):
            st.text("\n".join(logs))

        progress.progress(1.0)

    except Exception as e:
        st.error(str(e))

