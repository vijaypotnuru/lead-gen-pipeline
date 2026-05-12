#!/usr/bin/env python3
"""
Email Enricher v4 — Multi-strategy free email finder.
Strategies (tried in order until email found):
  1. Website scraping (/contact, /about, / pages) 
  2. SMTP handshake verification of common patterns (info@, contact@, etc.)
  3. Person-specific email guessing (if people data exists) via SMTP

No paid APIs. No rate limits. Just pattern matching + SMTP verification.
"""

import argparse, json, random, re, smtplib, socket, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import requests

LEADS_DIR = Path("/mnt/d/leads-folder")
DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]
TEAM_PAGES = ["/contact", "/about", "/about-us", "/"]
MIN_DELAY, MAX_DELAY = 0.5, 2.0
TIMEOUT = 12
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
MAX_LEADS_PER_RUN = 30
MIN_SCORE = 3
SMTP_TIMEOUT = 5
SMTP_PORTS = [25, 587]

GENERIC_PREFIXES = ["info", "contact", "hello", "hi", "support", "sales", "admin", "mail", "office", "team", "help"]

@dataclass
class Person:
    name: str = ""; role: str = ""; linkedin: str = ""
    twitter: str = ""; github: str = ""; email: str = ""
    def to_dict(self): return {k: v for k, v in asdict(self).items() if v}

@dataclass
class Lead:
    company: str = ""; website: str = ""; industry: str = ""; location: str = ""
    size: str = ""; signal: str = ""; signal_detail: str = ""
    contact_email: str = ""; linkedin: str = ""; phone: str = ""
    source: str = ""; score: int = 2; routing: str = "both"
    notes: str = ""; search_query: str = ""; city: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    people: List[Dict[str, str]] = field(default_factory=list)
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self); d.pop("_extra", None)
        for k, v in self._extra.items():
            if k not in d: d[k] = v
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "Lead":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs, extra = {}, {}
        for k, v in raw.items():
            (kwargs if k in known else extra)[k] = v
        lead = cls(**kwargs); lead._extra = extra
        return lead

# --- helpers ---

def normalize_url(raw: str) -> Optional[str]:
    if not raw: return None
    raw = raw.strip()
    if not raw.startswith("http"): raw = "https://" + raw
    try:
        p = urlparse(raw); return f"{p.scheme}://{p.netloc}" if p.netloc else None
    except: return None

# --- Strategy 1: Website scraping ---

def extract_emails_from_html(html: str) -> List[str]:
    import html as _hlib
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _hlib.unescape(text)

    emails, seen = [], set()
    for m in re.finditer(r'[\w.+-]+@[\w-]+\.[\w.]+', text, re.I):
        email = m.group(0).lower().strip(".")
        if email in seen: continue
        if any(s in email for s in ('example.com','sentry.io','w3.org','@pic.','@2x.','.png','.jpg')): continue
        if len(email) > 254: continue
        seen.add(email); emails.append(email)
    return emails

def scrape_website_emails(base_url: str, session: requests.Session) -> List[str]:
    all_emails = []
    # Try both www and non-www variants
    urls_to_try = [base_url]
    if base_url.startswith("http://www."):
        urls_to_try.append(base_url.replace("http://www.", "http://", 1))
    elif base_url.startswith("https://www."):
        urls_to_try.append(base_url.replace("https://www.", "https://", 1))

    for base in urls_to_try:
        for page in TEAM_PAGES:
            url = base.rstrip("/") + page if page != "/" else base
            try:
                r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA}, allow_redirects=True, verify=False)
                if r.status_code == 200 and len(r.text) > 300:
                    all_emails.extend(extract_emails_from_html(r.text))
                    if all_emails:
                        break
            except Exception:
                pass
        if all_emails:
            break
    return list(dict.fromkeys(all_emails))

# --- Strategy 2 & 3: SMTP ---

def get_mx_servers(domain: str) -> List[str]:
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, 'MX', lifetime=2)
        return [str(a.exchange).rstrip('.') for a in sorted(answers, key=lambda a: a.preference)]
    except Exception:
        pass
    return [f"mail.{domain}", domain]

def smtp_port_reachable(host: str) -> int:
    """Return first reachable SMTP port, or 0 if none."""
    for port in SMTP_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            r = s.connect_ex((host, port))
            s.close()
            if r == 0: return port
        except: pass
    return 0

def check_smtp_email(email: str, mx_host: str, port: int) -> bool:
    try:
        with smtplib.SMTP(mx_host, port, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo_or_helo_if_needed()
            smtp.mail("verify@example.com")
            code, _ = smtp.rcpt(email)
            return code == 250
    except: return False

def check_email_fast(email: str, mx_host: str) -> bool:
    port = smtp_port_reachable(mx_host)
    if not port: return False
    return check_smtp_email(email, mx_host, port)

def guess_generic_emails(domain: str, mx_host: str) -> List[str]:
    found = []
    for prefix in GENERIC_PREFIXES:
        email = f"{prefix}@{domain}"
        if check_email_fast(email, mx_host):
            found.append(email)
            if len(found) >= 2: break
    return found

def guess_person_email(name: str, domain: str, mx_host: str) -> str:
    if not name or not domain: return ""
    parts = name.lower().split()
    if len(parts) < 2: return ""
    first, last = parts[0], parts[-1]
    fi = first[0] if first else ""
    li = last[0] if last else ""

    patterns = [
        f"{first}.{last}@{domain}", f"{fi}{last}@{domain}", f"{first}@{domain}",
        f"{first}_{last}@{domain}", f"{fi}.{last}@{domain}", f"{first}{last}@{domain}",
        f"{first}.{li}@{domain}", f"{fi}_{last}@{domain}", f"{last}@{domain}",
    ]
    for email in patterns:
        if check_email_fast(email, mx_host):
            return email
    return ""

# --- Main enrich ---

def enrich_lead(lead: Lead, session: requests.Session) -> int:
    base = normalize_url(lead.website)
    if not base: return 0

    domain = urlparse(base).netloc.lower().lstrip("www.")
    if not domain: return 0

    emails_found = 0

    # Strategy 1: Website scraping
    if not lead.contact_email:
        scraped = scrape_website_emails(base, session)
        domain_emails = [e for e in scraped if domain in e.split("@")[-1]]
        if domain_emails:
            lead.contact_email = domain_emails[0]
            emails_found += 1
            extras = domain_emails[1:3]
            if extras:
                lead.notes = (lead.notes or "") + f" | More: {', '.join(extras)}"
                lead.notes = lead.notes.strip(" |")

    # Pick best MX server
    mx_hosts = get_mx_servers(domain)
    mx_host = mx_hosts[0] if mx_hosts else None

    # Strategy 2: SMTP generic guessing
    if not lead.contact_email and mx_host:
        generic = guess_generic_emails(domain, mx_host)
        if generic:
            lead.contact_email = generic[0]
            emails_found += 1

    # Strategy 3: Person-specific emails
    if lead.people and mx_host:
        for pdict in lead.people:
            if pdict.get("email"): continue
            name = pdict.get("name", "")
            guessed = guess_person_email(name, domain, mx_host)
            if guessed:
                pdict["email"] = guessed
                emails_found += 1

    return emails_found

# --- Orchestrate ---

def enrich_file(filepath: Path, max_leads: int, dry_run: bool, min_score: int) -> int:
    with open(filepath, "r", encoding="utf-8") as f:
        raw_leads = json.load(f)
    if not raw_leads:
        print(f"  {filepath.name}: empty, skipping"); return 0

    leads = [Lead.from_dict(r) for r in raw_leads]
    candidates = [l for l in leads if l.website and l.score >= min_score][:max_leads]
    print(f"\n📄 {filepath.name}: {len(candidates)}/{len(leads)} leads")

    session = requests.Session(); session.max_redirects = 5
    total_emails = enriched = 0

    for i, lead in enumerate(candidates, 1):
        short = lead.company[:45] if lead.company else "Unknown"
        print(f"  [{i}/{len(candidates)}] {short} ... ", end="", flush=True)

        new = enrich_lead(lead, session)
        total_emails += new

        if new > 0:
            enriched += 1
            parts = []
            if lead.contact_email: parts.append(f"company: {lead.contact_email}")
            pc = sum(1 for p in lead.people if p.get("email"))
            if pc: parts.append(f"{pc} people")
            print(f"✔ {' | '.join(parts)}")
        elif lead.contact_email:
            print("has email")
        else:
            print("✗")

        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    if not dry_run and total_emails > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
        print(f"  💾 Saved ({total_emails} new)")

    t = len([l for l in leads if l.contact_email])
    tp = sum(len([p for p in l.people if p.get("email")]) for l in leads)
    print(f"  📊 Companies w/ email: {t}/{len(leads)} | Person emails: {tp}")
    return total_emails

def main():
    p = argparse.ArgumentParser(description="Email Enricher v4")
    p.add_argument("--source", default=None)
    p.add_argument("--max", type=int, default=MAX_LEADS_PER_RUN)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-score", type=int, default=MIN_SCORE)
    args = p.parse_args()

    files = [LEADS_DIR / args.source] if args.source else [LEADS_DIR / f for f in DEFAULT_FILES]
    files = [f for f in files if f.exists()]
    if not files: print("No files."); return

    start = time.time(); total = 0
    for fp in files: total += enrich_file(fp, args.max, args.dry_run, args.min_score)
    print(f"\n✅ {total} emails in {time.time()-start:.0f}s")

if __name__ == "__main__": main()
