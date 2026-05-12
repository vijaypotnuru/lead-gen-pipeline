#!/usr/bin/env python3
"""
Scrapling Enricher v1 — Unified person + email discovery using Scrapling.
Features: find_by_text (smart text search), find_similar (related elements),
auto_save (survive site redesigns), StealthyFetcher (anti-bot bypass).

Replaces the old regex-only person-enricher.py and email-enricher-v4.py
with a single Scrapling-native pipeline.
"""

import argparse, asyncio, json, random, re, smtplib, socket, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from scrapling import StealthyFetcher

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LEADS_DIR = Path("/mnt/d/leads-folder")
DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]
MAX_LEADS_PER_RUN = 30
MIN_SCORE = 3
SMTP_TIMEOUT = 5
CONCURRENCY = 3  # parallel leads

ROLE_KEYWORDS = ["CEO","Founder","Co-Founder","CTO","CMO","COO","CFO","Director",
                 "VP","President","Chairman","Managing Director","Head of","Manager",
                 "Lead","Principal","Partner","Owner"]

GENERIC_EMAILS = ["info","contact","hello","sales","support","admin","mail"]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def norm_url(raw: str) -> Optional[str]:
    if not raw: return None
    raw = raw.strip()
    if not raw.startswith("http"): raw = "https://" + raw
    try:
        p = urlparse(raw); return f"{p.scheme}://{p.netloc}" if p.netloc else None
    except: return None

def looks_like_name(text: str) -> bool:
    """Person name heuristic — stricter for Scrapling output."""
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 50: return False
    words = text.split()
    if len(words) < 2 or len(words) > 4: return False
    # Reject known non-name patterns
    noise = {'and','the','of','in','for','our','we','is','are','to','at','be','an',
             'inc','ltd','llc','co','pvt','private','limited','group','solutions',
             'technologies','services','consulting','agency','studio','software',
             'digital','marketing','media','design','development','security','cloud',
             'company','brand','business','system','international','global',
             'traffic','management','compliance','environmental','regulations',
             'values','ethics','product','quality','process','control','innovation',
             'strategy','creative','content','social','mobile','web','app','network',
             'infrastructure','platform','analytics','growth','performance',
             'experience','transform','excellence','agile','professional',
             'partner','client','customer','engineer','architecture','framework'}
    for w in words:
        if w.lower() in noise: return False
        if len(w) > 15: return False
        if not w[0].isalpha() or not w[0].isupper(): return False
    cap = [w for w in words if w[0].isupper() and len(w) >= 2]
    return len(cap) >= 2

# ---------------------------------------------------------------------------
# Email: Website scrape + SMTP
# ---------------------------------------------------------------------------

def get_mx_servers(domain: str) -> List[str]:
    try:
        import dns.resolver
        a = dns.resolver.resolve(domain, 'MX')
        return [str(x.exchange).rstrip('.') for x in sorted(a, key=lambda a: a.preference)]
    except: return []

def smtp_verify(email: str, host: str, port: int) -> bool:
    try:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as s:
            s.ehlo_or_helo_if_needed()
            s.mail("v@example.com")
            return s.rcpt(email)[0] == 250
    except: return False

def find_emails_scrapling(page_text: str) -> List[str]:
    """Regex extraction from page text (Scrapling's get_all_text)"""
    emails, seen = [], set()
    for m in re.finditer(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', page_text, re.I):
        e = m.group(0).lower().strip(".")
        if e in seen or len(e) > 254: continue
        if any(s in e for s in ('example.com','sentry.io','w3.org','@pic.','.png','.jpg')): continue
        seen.add(e); emails.append(e)
    return emails

# ---------------------------------------------------------------------------
# Person discovery using Scrapling features
# ---------------------------------------------------------------------------

def discover_people_from_page(page) -> List[Person]:
    """Use Scrapling's find_by_text + find_similar to locate team members."""
    people: List[Person] = []

    # Step 1: Find team/leadership section by text
    team = None
    for label in ['Our Team', 'Team', 'Leadership', 'Management', 'About Us', 'Meet the Team', 'Our People']:
        try:
            found = page.find_by_text(label)
            if found:
                team = found
                break
        except: pass

    if not team:
        team = page  # search entire page as fallback

    # Step 2: Try find_similar on the team section to get person cards
    person_elements = []
    try:
        # Find one element containing a role keyword, then find similar
        for role in ROLE_KEYWORDS[:3]:
            try:
                ref = team.find_by_text(role)
                if ref:
                    similar = team.find_similar(ref, threshold=0.6)
                    person_elements.extend(similar)
                    break
            except: pass
    except: pass

    # Step 3: If find_similar didn't work, try text-based extraction
    if not person_elements:
        try:
            all_text = page.get_all_text() or ""
            lines = [l.strip() for l in all_text.split('\n') if l.strip()]
            for i, line in enumerate(lines):
                for role in ROLE_KEYWORDS:
                    if role.lower() in line.lower():
                        # Try extracting name before the role
                        idx = line.lower().index(role.lower())
                        before = line[:idx].strip()
                        # Take last 2-3 words as potential name
                        parts = before.split()
                        for n in [3, 2]:
                            if len(parts) >= n:
                                candidate = ' '.join(parts[-n:]).strip(' ,.;:-')
                                if looks_like_name(candidate):
                                    p = Person(name=candidate, role=line[idx:].strip()[:60])
                                    if not any(x.name.lower() == p.name.lower() for x in people):
                                        people.append(p)
                                    break
                        break
                if len(people) >= 5:
                    break
        except: pass

    # Step 4: For each person element from find_similar, extract name + role
    for el in person_elements[:5]:
        try:
            text = el.get_all_text() or ""
            # Find a name pattern
            name_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})', text)
            role_match = next((r for r in ROLE_KEYWORDS if r.lower() in text.lower()), None)
            if name_match and role_match:
                p = Person(name=name_match.group(1))
                idx = text.lower().index(role_match.lower())
                p.role = text[idx:idx+60].strip()
                if not any(x.name.lower() == p.name.lower() for x in people):
                    people.append(p)
        except: pass

    return people[:5]

# ---------------------------------------------------------------------------
# Main enrich function (per lead)
# ---------------------------------------------------------------------------

def enrich_single_lead(lead: Lead) -> int:
    """Enrich one lead using Scrapling. Returns number of new items found."""
    base = norm_url(lead.website)
    if not base: return 0

    domain = urlparse(base).netloc.lower().lstrip("www.")
    if not domain: return 0

    new_items = 0

    # Fetch with StealthyFetcher
    try:
        StealthyFetcher.adaptive = True
        page = StealthyFetcher.fetch(base, headless=True, network_idle=True, wait=3000, timeout=20000)
        all_text = page.get_all_text() or ""
    except Exception:
        return 0

    # --- Email Discovery ---
    if not lead.contact_email:
        # Extract from page
        emails = find_emails_scrapling(all_text)
        domain_emails = [e for e in emails if domain in e.split('@')[-1]]

        if domain_emails:
            lead.contact_email = domain_emails[0]
            new_items += 1
        else:
            # SMTP guessing fallback
            mx = get_mx_servers(domain)
            if mx and mx[0]:
                host = mx[0]
                for pfx in GENERIC_EMAILS:
                    e = f"{pfx}@{domain}"
                    # Quick port check
                    for port in [25, 587]:
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(2)
                            if s.connect_ex((host, port)) == 0:
                                s.close()
                                if smtp_verify(e, host, port):
                                    lead.contact_email = e
                                    new_items += 1
                                    break
                                break
                            s.close()
                        except: pass
                    if lead.contact_email: break

    # --- Person Discovery ---
    if not lead.people:
        people = discover_people_from_page(page)
        if people:
            lead.people = [p.to_dict() for p in people]
            new_items += len(people)

    # --- Person email guessing ---
    if lead.people:
        mx = get_mx_servers(domain)
        if mx and mx[0]:
            host = mx[0]
            for pdict in lead.people:
                if pdict.get("email"): continue
                name = pdict.get("name", "")
                parts = name.lower().split()
                if len(parts) < 2: continue
                f, l = parts[0], parts[-1]
                fi = f[0] if f else ""
                patterns = [
                    f"{f}.{l}@{domain}", f"{fi}{l}@{domain}", f"{f}@{domain}",
                    f"{f}_{l}@{domain}", f"{fi}.{l}@{domain}", f"{l}@{domain}",
                ]
                for e in patterns:
                    for port in [25, 587]:
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(2)
                            if s.connect_ex((host, port)) == 0:
                                s.close()
                                if smtp_verify(e, host, port):
                                    pdict["email"] = e; new_items += 1
                                    break
                            s.close()
                        except: pass
                    if pdict.get("email"): break

    return new_items

# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def process_file(filepath: Path, max_leads: int, dry_run: bool, min_score: int) -> int:
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not raw:
        print(f"  {filepath.name}: empty"); return 0

    leads = [Lead.from_dict(r) for r in raw]
    candidates = [l for l in leads if l.website and l.score >= min_score][:max_leads]

    print(f"\n📄 {filepath.name}: {len(candidates)}/{len(leads)} leads")
    total = 0
    enriched = 0

    for i, lead in enumerate(candidates, 1):
        name = lead.company[:45] if lead.company else "?"
        status = "has both" if lead.contact_email and lead.people else ("has email" if lead.contact_email else "searching")
        print(f"  [{i}/{len(candidates)}] {name} ({status}) ... ", end="", flush=True)

        if lead.contact_email and lead.people:
            print("skip")
            continue

        try:
            new = enrich_single_lead(lead)
            total += new
            if new > 0: enriched += 1
            parts = []
            if lead.contact_email: parts.append(f"email: {lead.contact_email}")
            if lead.people: parts.append(f"{len(lead.people)} people")
            print(f"{'✔' if new or lead.contact_email else '✗'} {' | '.join(parts) if parts else ''}")
        except Exception as e:
            print(f"✗ {e}")

        time.sleep(random.uniform(2, 5))

    if not dry_run and total > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
        print(f"  💾 Saved ({total} new items)")

    e = len([l for l in leads if l.contact_email])
    p = len([l for l in leads if l.people])
    pe = sum(len([p for p in l.people if p.get("email")]) for l in leads)
    print(f"  📊 Email: {e}/{len(leads)} | People: {p}/{len(leads)} | Person emails: {pe}")
    return total

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=None)
    p.add_argument("--max", type=int, default=MAX_LEADS_PER_RUN)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-score", type=int, default=MIN_SCORE)
    args = p.parse_args()

    files = [LEADS_DIR / args.source] if args.source else [LEADS_DIR / f for f in DEFAULT_FILES]
    files = [f for f in files if f.exists()]
    if not files: print("No files."); return

    start = time.time(); total = 0
    for fp in files: total += process_file(fp, args.max, args.dry_run, args.min_score)
    print(f"\n✅ {total} new items in {time.time()-start:.0f}s")

if __name__ == "__main__": main()
