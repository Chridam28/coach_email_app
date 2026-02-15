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

# ============================ PAGE SETUP (CLEAN & ACADEMIC) ============================

st.set_page_config(
    page_title="Coach Contact Extractor",
    page_icon="📚",
    layout="centered",
)

col1, col2 = st.columns([1, 3])

with col1:
    st.image("logo.jpg", width=130)

with col2:
    st.markdown(
        """
        <h1 style="margin-bottom:0.2rem;">Coach Contact Extractor</h1>
        <div style="color:#6b7280; font-size:0.95rem;">
        Academic email extraction tool for collegiate athletics programs
        </div>
        """,
        unsafe_allow_html=True
    )


# ============================ SCRAPER LOGIC (UNCHANGED) ============================

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

OBFUSCATIONS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\s*\[at\]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*\(at\)\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s+at\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s*\[dot\]\s*", re.IGNORECASE), "."),
    (re.compile(r"\s*\(dot\)\s*", re.IGNORECASE), "."),
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
]

TARGET_ROLE_KEYWORDS = [
    "head coach",
    "assistant coach",
    "asst coach",
    "associate head coach",
    "associate coach",
    "interim head coach",
    "coach",
    "recruiting",
    "recruiting coordinator",
    "recruiting coord",
    "director of recruiting",
    "recruiting director",
    "recruiting operations",
    "recruiting ops",
    "coordinator of recruiting",
]

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

def sport_tokens(sport: str) -> Set[str]:
    s = sport.strip().lower()
    s_clean = re.sub(r"[^a-z0-9\s']", " ", s)
    s_clean = re.sub(r"\s+", " ", s_clean).strip()

    tokens: Set[str] = set()
    if s_clean:
        tokens.add(s_clean)
        tokens.add(s_clean.replace("’", "'"))
        tokens.add(s_clean.replace("'", ""))

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

    html = fetch(session, t.url)
    emails.update(extract_target_emails_from_page(html, t.url, t.sport, require_sport_match=False))

    if not emails:
        emails.update(extract_from_bios(session, t.url, html, t.sport, require_sport_match=False))

    if not emails and t.staff_directory_url.strip():
        sdu = t.staff_directory_url.strip()
        sd_html = fetch(session, sdu)

        emails.update(extract_target_emails_from_page(sd_html, sdu, t.sport, require_sport_match=True))

        if not emails:
            emails.update(extract_from_bios(session, sdu, sd_html, t.sport, require_sport_match=True))

    time.sleep(sleep_s)
    return emails

# ============================ UI CONTROLS (CLEAN) ============================

st.markdown("### Input")
uploaded = st.file_uploader(
    "Upload a CSV with columns: university, sport, url, staff_directory_url (optional)",
    type=["csv"]
)

with st.expander("Settings", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        sleep_s = st.number_input("Pause between universities (seconds)", 0.0, 10.0, 1.2, 0.2)
    with c2:
        max_rows = st.number_input("Limit rows (0 = no limit)", 0, 50000, 0, 10)

    st.caption("Output is saved with ';' as column delimiter to avoid quoting emails that contain commas.")

run_btn = st.button("Run extraction", type="primary", use_container_width=True, disabled=(uploaded is None))

# ============================ RUN + PROGRESS ============================

if run_btn and uploaded is not None:
    try:
        data = uploaded.getvalue()

        text = data.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        needed = {"university", "sport", "url"}
        if not reader.fieldnames or not needed.issubset(set(reader.fieldnames)):
            st.error("Input CSV must contain columns: university, sport, url (optional: staff_directory_url).")
            st.stop()

        targets: List[Target] = []
        for row in reader:
            u = (row.get("university") or "").strip()
            s = (row.get("sport") or "").strip()
            url = (row.get("url") or "").strip()
            sd = (row.get("staff_directory_url") or "").strip()
            if u and s and url:
                targets.append(Target(university=u, sport=s, url=url, staff_directory_url=sd))

        if max_rows > 0:
            targets = targets[:max_rows]

        total = len(targets)
        if total == 0:
            st.error("No valid rows found in the CSV.")
            st.stop()

        session = make_session()

        st.markdown("### Progress")
        progress_bar = st.progress(0.0)
        status = st.empty()

        results = []
        logs = []

        for idx, t in enumerate(targets, start=1):
            status.markdown(
                f"**University:** {t.university}  \n"
                f"**Sport:** {t.sport}  \n"
                f"**Step:** {idx}/{total}"
            )

            emails = process_one_target(session, t, sleep_s=float(sleep_s))
            results.append({"university": t.university, "emails": join_emails(emails)})
            logs.append(f"[{idx}/{total}] {t.university} -> {len(emails)} email(s)")

            progress_bar.progress(idx / total)

        # Build output CSV with ';' delimiter
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["university", "emails"], delimiter=";")
        writer.writeheader()
        writer.writerows(results)

        st.success("Completed.")
        st.download_button(
            label="Download output.csv",
            data=output.getvalue(),
            file_name="output.csv",
            mime="text/csv",
            use_container_width=True,
        )

        with st.expander("Log"):
            st.text("\n".join(logs))

    except Exception as e:
        st.error(str(e))

st.markdown(
    "<hr><div style='text-align:center; color:#6b7280; font-size:0.85rem;'>Internal tool • Coach Contact Extractor</div>",
    unsafe_allow_html=True,
)
