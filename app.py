import streamlit as st
import csv
import re
import io
import time
from dataclasses import dataclass
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

st.set_page_config(page_title="Coach Email Extractor", layout="wide")

# ---------------- CONFIG ----------------

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
    "recruiting coordinator",
    "director of recruiting",
    "recruiting director",
    "recruiting operations",
]

EXCLUDE_ROLE_KEYWORDS = [
    "student assistant",
    "graduate assistant",
    "grad assistant",
]

@dataclass
class Target:
    university: str
    sport: str
    url: str
    staff_directory_url: str = ""

# ---------------- UTILS ----------------

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0"
    })
    return s

def deobfuscate(text):
    for pat, repl in OBFUSCATIONS:
        text = pat.sub(repl, text)
    return text

def norm(s):
    return re.sub(r"\s+", " ", s.strip().lower())

def extract_emails(soup):
    emails = set()

    for a in soup.select('a[href^="mailto:"]'):
        e = a.get("href", "").replace("mailto:", "").split("?")[0]
        emails.add(e)

    text = deobfuscate(soup.get_text(" ", strip=True))
    emails.update(EMAIL_RE.findall(text))

    return {e.strip() for e in emails if e.strip()}

def is_excluded(text):
    return any(k in text for k in EXCLUDE_ROLE_KEYWORDS)

def is_target(text):
    if is_excluded(text):
        return False
    return any(k in text for k in TARGET_ROLE_KEYWORDS)

def sport_tokens(sport):
    s = sport.lower()
    s = re.sub(r"[^a-z0-9\s']", " ", s)
    return {w for w in s.split() if len(w) > 3}

def sport_match(text, sport):
    tokens = sport_tokens(sport)
    if not tokens:
        return True
    return any(tok in text for tok in tokens)

def find_blocks(soup):
    blocks = soup.select("table tr")
    if len(blocks) > 5:
        return blocks
    return soup.find_all("div")

def extract_from_page(session, url, sport, require_sport_match):
    html = session.get(url).text
    soup = BeautifulSoup(html, "lxml")

    emails = set()
    blocks = find_blocks(soup)

    for b in blocks:
        text = norm(b.get_text(" ", strip=True))
        if not is_target(text):
            continue
        if require_sport_match and not sport_match(text, sport):
            continue
        emails.update(extract_emails(b))

    return emails

def extract_from_bios(session, url, sport, require_sport_match):
    html = session.get(url).text
    soup = BeautifulSoup(html, "lxml")
    emails = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "coach" in href or "bio" in href:
            full = urljoin(url, href)
            try:
                bio_html = session.get(full).text
                bio_soup = BeautifulSoup(bio_html, "lxml")
                text = norm(bio_soup.get_text(" ", strip=True))
                if is_excluded(text):
                    continue
                if require_sport_match and not sport_match(text, sport):
                    continue
                emails.update(extract_emails(bio_soup))
                time.sleep(0.5)
            except:
                pass

    return emails

def join_emails(emails):
    return ", ".join(sorted(emails, key=lambda x: x.lower()))

# ---------------- STREAMLIT UI ----------------

st.title("🏀 Coach Email Extractor (Web Version)")

uploaded_file = st.file_uploader("Carica il file CSV", type=["csv"])

if uploaded_file:

    content = uploaded_file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))

    required_cols = {"university", "sport", "url"}
    if not required_cols.issubset(reader.fieldnames):
        st.error("Il CSV deve contenere: university, sport, url (+ opzionale staff_directory_url)")
        st.stop()

    targets = []
    for row in reader:
        targets.append(Target(
            university=row["university"],
            sport=row["sport"],
            url=row["url"],
            staff_directory_url=row.get("staff_directory_url", "")
        ))

    if st.button("🚀 Esegui estrazione"):
        session = make_session()
        results = []

        progress = st.progress(0)

        for i, t in enumerate(targets):
            emails = set()

            # Sport page
            emails.update(extract_from_page(session, t.url, t.sport, False))

            if not emails:
                emails.update(extract_from_bios(session, t.url, t.sport, False))

            if not emails and t.staff_directory_url:
                emails.update(extract_from_page(session, t.staff_directory_url, t.sport, True))
                if not emails:
                    emails.update(extract_from_bios(session, t.staff_directory_url, t.sport, True))

            results.append({
                "university": t.university,
                "emails": join_emails(emails)
            })

            progress.progress((i + 1) / len(targets))

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["university", "emails"], delimiter=";")
        writer.writeheader()
        writer.writerows(results)

        st.success("Completato!")

        st.download_button(
            label="📥 Scarica CSV risultato",
            data=output.getvalue(),
            file_name="output.csv",
            mime="text/csv"
        )
