#!/usr/bin/env python3
"""
Email Enricher v4 — Multi-strategy cascade with timeouts.
Fast: per-lead 15s timeout, DNS pre-check, quick SMTP, minimal patterns.
"""

import argparse, json, random, re, smtplib, socket, signal, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import requests

LEADS_DIR = Path("/mnt/d/leads-folder")
DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]
TEAM_PAGES = ["/contact", "/about", "/about-us", "/"]
MIN_DELAY, MAX_DELAY = 0.3, 1.0
TIMEOUT = 5
SMTP_TIMEOUT = 2
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
MAX_LEADS_PER_RUN = 30
MIN_SCORE = 2
PER_LEAD_TIMEOUT = 15  # seconds max per lead

GENERIC_PREFIXES = ["info", "contact", "hello", "hi", "support", "sales", "admin", "mail", "help"]

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

def get_domain_from_url(url: str) -> str:
    p = urlparse(url)
    d = p.netloc.lower()
    return d[4:] if d.startswith("www.") else d

class TimeoutError(Exception): pass

def _alarm_handler(signum, frame):
    raise TimeoutError("Lead enrichment timed out")

def with_timeout(seconds: int, func, *args, **kwargs):
    """Run func with a timeout using signal alarm (Unix only)."""
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(seconds)
    try:
        return func(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

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
    domain = get_domain_from_url(base_url)
    # Try both with and without www prefix
    variants = [base_url]
    if base_url.startswith("https://www."):
        variants.append(base_url.replace("https://www.", "https://", 1))
    elif base_url.startswith("http://www."):
        variants.append(base_url.replace("http://www.", "http://", 1))
    elif base_url.startswith("https://"):
        variants.append(base_url.replace("https://", "https://www.", 1))
    elif base_url.startswith("http://"):
        variants.append(base_url.replace("http://", "http://www.", 1))

    for base in variants:
        for page in TEAM_PAGES:
            url = base.rstrip("/") + page if page != "/" else base
            try:
                r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA},
                                allow_redirects=True, verify=False)
                if r.status_code == 200 and len(r.text) > 300:
                    emails = extract_emails_from_html(r.text)
                    domain_emails = [e for e in emails if domain in e.split("@")[-1]]
                    all_emails.extend(domain_emails)
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
        hosts = [str(a.exchange).rstrip('.') for a in sorted(answers, key=lambda a: a.preference)]
        # Verify at least one resolves
        for h in hosts:
            try:
                socket.getaddrinfo(h, None)
                return hosts
            except socket.gaierror:
                pass
    except Exception:
        pass
    try:
        socket.getaddrinfo(f"mail.{domain}", None)
        return [f"mail.{domain}"]
    except socket.gaierror:
        pass
    return []

def check_smtp_email(email: str, mx_host: str) -> bool:
    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo_or_helo_if_needed()
            smtp.mail("verify@example.com")
            code, _ = smtp.rcpt(email)
            return code == 250
    except Exception:
        return False

def guess_generic_emails(domain: str, mx_hosts: List[str]) -> List[str]:
    if not mx_hosts:
        return []
    found = []
    mx = mx_hosts[0]
    for prefix in GENERIC_PREFIXES:
        email = f"{prefix}@{domain}"
        if check_smtp_email(email, mx):
            found.append(email)
            if len(found) >= 2:
                break
        time.sleep(0.05)
    return found

def guess_person_email(name: str, domain: str, mx_hosts: List[str]) -> str:
    if not name or not domain or not mx_hosts:
        return ""
    name = re.sub(r'\s+\d+[a-z0-9]+$', '', name.strip(), flags=re.I)
    parts = name.lower().split()
    if len(parts) < 2:
        return ""
    first, last = parts[0], parts[-1]
    fi = first[0] if first else ""

    patterns = [
        f"{first}.{last}@{domain}",
        f"{fi}{last}@{domain}",
        f"{first}@{domain}",
        f"{first}_{last}@{domain}",
        f"{last}@{domain}",
    ]
    mx = mx_hosts[0]
    for email in patterns:
        if check_smtp_email(email, mx):
            return email
        time.sleep(0.05)
    return ""

# --- Main enrich ---

def enrich_lead_core(lead: Lead, session: requests.Session) -> int:
    base = normalize_url(lead.website)
    if not base:
        return 0

    domain = get_domain_from_url(base)
    if not domain:
        return 0

    emails_found = 0

    # Strategy 1: Website scraping
    if not lead.contact_email:
        scraped = scrape_website_emails(base, session)
        if scraped:
            lead.contact_email = scraped[0]
            emails_found += 1
            if len(scraped) > 1:
                lead.notes = (lead.notes or "") + f" | More: {', '.join(scraped[1:3])}"
                lead.notes = lead.notes.strip(" |")

    # Strategy 2: SMTP generic
    if not lead.contact_email:
        mx_hosts = get_mx_servers(domain)
        if mx_hosts:
            generic = guess_generic_emails(domain, mx_hosts)
            if generic:
                lead.contact_email = generic[0]
                emails_found += 1

    # Strategy 3: Person-specific emails
    if lead.people:
        mx_hosts = get_mx_servers(domain)
        if mx_hosts:
            for pdict in lead.people:
                if pdict.get("email"):
                    continue
                name = pdict.get("name", "")
                if not name or len(name) < 3 or ' ' not in name:
                    continue
                guessed = guess_person_email(name, domain, mx_hosts)
                if guessed:
                    pdict["email"] = guessed
                    emails_found += 1

    return emails_found

def enrich_lead(lead: Lead, session: requests.Session) -> int:
    """Wrapper with timeout."""
    try:
        return with_timeout(PER_LEAD_TIMEOUT, enrich_lead_core, lead, session)
    except TimeoutError:
        return 0
    except Exception:
        return 0

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
