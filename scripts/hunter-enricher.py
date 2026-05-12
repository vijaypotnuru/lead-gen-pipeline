#!/usr/bin/env python3
"""
Hunter.io Email Enricher — Finds PERSON emails using Hunter.io free API (25 searches/month).
Replaces unreliable SMTP guessing with a real email discovery API.

Setup:
  1. Sign up at https://hunter.io/users/sign_up (free, no credit card)
  2. Go to https://hunter.io/dashboard → API → copy your API key
  3. Set: export HUNTER_API_KEY="your_key_here"

Usage:
  python3 scripts/hunter-enricher.py --source bangalore_test_leads.json --min-score 2
"""

import argparse, json, os, random, re, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

LEADS_DIR = Path("/mnt/d/leads-folder")
DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]
MAX_PER_RUN = 25
MIN_SCORE = 3

HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
HUNTER_API = "https://api.hunter.io/v2"

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
        kw = {}; ex = {}
        for k, v in raw.items():
            (kw if k in known else ex)[k] = v
        l = cls(**kw); l._extra = ex; return l

# ─── Hunter.io API ────────────────────────────────────────────────

def hunter_domain_search(domain: str) -> Optional[dict]:
    """Search all emails for a domain (25/month on free tier)."""
    try:
        import requests
        r = requests.get(
            f"{HUNTER_API}/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY},
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        print(f"    Hunter API error: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        print(f"    Hunter API failed: {e}")
    return None

def hunter_email_find(domain: str, first_name: str, last_name: str) -> Optional[dict]:
    """Find one person's email (counts as 1 search on free tier)."""
    try:
        import requests
        r = requests.get(
            f"{HUNTER_API}/email-finder",
            params={
                "domain": domain, "first_name": first_name, "last_name": last_name,
                "api_key": HUNTER_API_KEY
            },
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

# ─── Enrichment ───────────────────────────────────────────────────

def enrich_lead(lead: Lead) -> int:
    base = lead.website.strip()
    if not base: return 0
    if not base.startswith("http"): base = "https://" + base

    try:
        domain = urlparse(base).netloc.lower().lstrip("www.")
    except:
        return 0

    new = 0
    print(f"    Domain: {domain}")

    # Strategy 1: Domain search (get ALL emails Hunter knows for this domain)
    result = hunter_domain_search(domain)
    if result and result.get("data"):
        data = result["data"]
        emails_list = data.get("emails", [])

        # Pick company email
        if not lead.contact_email and emails_list:
            # Prefer: info@, contact@, hello@, sales@ over generic ones
            priority = next((e for e in emails_list if e.get("value","").startswith(("info@","contact@","hello@","sales@"))), emails_list[0])
            lead.contact_email = priority.get("value", "")
            new += 1

        # Pick person emails from domain search
        person_emails = [e for e in emails_list if e.get("first_name") or e.get("last_name")]
        for pe in person_emails[:5]:
            p = Person(
                name=f"{pe.get('first_name','')} {pe.get('last_name','')}".strip(),
                role=pe.get("position", ""),
                linkedin=pe.get("linkedin", ""),
                email=pe.get("value", ""),
            )
            if p.name and not any(x.get("email","") == p.email for x in lead.people):
                lead.people.append(p.to_dict())
                new += 1

        # Show what we got
        print(f"    Found {len(emails_list)} emails total, {len(person_emails)} person emails")
        if person_emails:
            for pe in person_emails[:3]:
                fn = pe.get("first_name","")
                ln = pe.get("last_name","")
                em = pe.get("value","")
                conf = pe.get("confidence", 0)
                print(f"      ✅ {fn} {ln} → {em} ({conf}% confidence)")
    else:
        # Strategy 2: Email finder per person (if we already have names)
        if lead.people:
            for pdict in lead.people:
                name = pdict.get("name", "")
                parts = name.split()
                if len(parts) >= 2:
                    first, last = parts[0], parts[-1]
                    result = hunter_email_find(domain, first, last)
                    if result and result.get("data"):
                        em = result["data"].get("email", "")
                        conf = result["data"].get("confidence", 0)
                        if em:
                            pdict["email"] = em
                            new += 1
                            print(f"      ✅ {name} → {em} ({conf}% confidence)")

    return new

# ─── Batch ───────────────────────────────────────────────────────

def process_file(fp: Path, max_l: int, dry: bool, ms: int) -> int:
    with open(fp, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not raw:
        print(f"  {fp.name}: empty"); return 0

    leads = [Lead.from_dict(r) for r in raw]
    candidates = [l for l in leads if l.website and l.score >= ms]

    # Filter: only leads without person emails
    need = [l for l in candidates if not any(p.get("email") for p in l.get("people", []))][:max_l]
    print(f"\n📄 {fp.name}: {len(need)}/{len(leads)} need person emails (API quota: {MAX_PER_RUN}/month)")

    total = 0
    for i, lead in enumerate(need, 1):
        nm = lead.company[:45] if lead.company else "?"
        has_company = "✓" if lead.contact_email else ""
        print(f"\n  [{i}/{len(need)}] {nm} {has_company}")

        try:
            n = enrich_lead(lead)
            total += n
        except Exception as e:
            print(f"    ❌ Error: {e}")

        time.sleep(random.uniform(1, 2))

    if not dry and total > 0:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
        print(f"\n  💾 Saved ({total} new items)")

    emails = len([l for l in leads if l.contact_email])
    people = len([l for l in leads if l.people])
    person_emails = sum(len([p for p in l.people if p.get("email")]) for l in leads)
    print(f"  📊 Company emails: {emails}/{len(leads)} | People w/ email: {person_emails}")
    return total

def main():
    p = argparse.ArgumentParser(description="Hunter.io Email Enricher")
    p.add_argument("--source", default=None)
    p.add_argument("--max", type=int, default=MAX_PER_RUN)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-score", type=int, default=MIN_SCORE)
    args = p.parse_args()

    if not HUNTER_API_KEY:
        print("❌ HUNTER_API_KEY not set!")
        print("   1. Sign up: https://hunter.io/users/sign_up")
        print("   2. Copy your API key from Dashboard → API")
        print("   3. Run: export HUNTER_API_KEY='your_key'")
        return

    files = [LEADS_DIR / args.source] if args.source else [LEADS_DIR / f for f in DEFAULT_FILES]
    files = [f for f in files if f.exists()]
    if not files:
        print("No files found.")
        return

    s = time.time(); t = 0
    for fp in files:
        t += process_file(fp, args.max, args.dry_run, args.min_score)
    print(f"\n✅ {t} person emails found in {time.time()-s:.0f}s")

if __name__ == "__main__":
    main()
