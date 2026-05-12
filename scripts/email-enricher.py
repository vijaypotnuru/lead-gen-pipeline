#!/usr/bin/env python3
"""
Email Enricher v3 — Fast hybrid: requests first, StealthyFetcher fallback.
Processes 100 leads per file in ~5 minutes.
"""

import json, random, re, time, requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

LEADS_DIR = Path("/mnt/d/leads-folder")
LEADS_DIR.mkdir(parents=True, exist_ok=True)

MIN_DELAY, MAX_DELAY = 0.5, 2.0
TIMEOUT = 10
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"

EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', re.I)
SKIP_DOMAINS = {'example.com', 'test.com', 'sentry.io', 'w3.org', 'schema.org', 'github.com', 'yourcompany.com'}
PAGES = ['/', '/contact', '/contact-us', '/about', '/about-us']


def find_emails(text: str, domain: str) -> list:
    found = EMAIL_RE.findall(text.lower())
    valid = [e for e in found if not any(d in e for d in SKIP_DOMAINS)]
    seen = set(); result = []
    for e in valid:
        e_domain = e.split('@')[-1]
        if domain in e_domain or e_domain in domain:
            if e not in seen: seen.add(e); result.append(e)
    for e in valid:
        if e not in seen: seen.add(e); result.append(e)
    return result[:5]


def scrape_site(base_url: str, domain: str) -> str:
    """Fetch homepage + contact pages, merge text."""
    headers = {"User-Agent": UA}
    all_text = ""
    session = requests.Session()
    session.max_redirects = 5
    
    for page in PAGES[:4]:
        try:
            # Build URL carefully
            if page == '/':
                url = base_url
            else:
                url = base_url.rstrip('/') + page
            
            r = session.get(url, timeout=TIMEOUT, headers=headers, allow_redirects=True)
            if r.status_code != 200:
                continue
            
            # Extract text from HTML
            html = r.text
            # Remove scripts, styles, HTML tags
            clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
            clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.I)
            clean = re.sub(r'<[^>]+>', ' ', clean)
            clean = re.sub(r'\s+', ' ', clean)
            all_text += " " + clean
            
            # If we already found emails, stop
            if EMAIL_RE.search(all_text):
                break
        except Exception:
            continue
    
    return all_text


def enrich_lead(lead: dict, idx: int, total: int) -> dict:
    if lead.get('contact_email'): return lead
    
    company = lead.get('company', '')[:50]
    raw_url = lead.get('website', '')
    if not raw_url: return lead
    
    # Normalize URL
    if not raw_url.startswith('http'):
        raw_url = 'https://' + raw_url
    
    parsed = urlparse(raw_url)
    domain = parsed.netloc.lower().lstrip('www.')
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    
    print(f"  [{idx}/{total}] {company}... ", end="", flush=True)
    
    text = scrape_site(base_url, domain)
    if not text:
        print("✗ (no content)")
        return lead
    
    emails = find_emails(text, domain)
    if emails:
        priority = [e for e in emails if any(e.startswith(p) for p in ('info@','hello@','contact@','hi@','mail@','support@','sales@'))]
        best = priority[0] if priority else emails[0]
        lead['contact_email'] = best
        lead['notes'] = (lead.get('notes', '') + f" | Emails: {','.join(emails[:3])}").strip(' |')
        print(f"✔ {best}")
        return lead
    
    print("✗")
    return lead


def run(input_file: str, max_leads: int = 200):
    path = LEADS_DIR / input_file
    with open(path) as f:
        leads = json.load(f)
    
    candidates = [l for l in leads if l.get('website') and l.get('score', 0) >= 4 and not l.get('contact_email')]
    candidates = candidates[:max_leads]
    
    print(f"\n{input_file}: {len(candidates)} leads to enrich")
    
    enriched = 0
    for i, lead in enumerate(candidates, 1):
        orig_idx = leads.index(lead)
        leads[orig_idx] = enrich_lead(lead.copy(), i, len(candidates))
        if leads[orig_idx].get('contact_email'):
            enriched += 1
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
    
    emails_count = len([l for l in leads if l.get('contact_email')])
    print(f"  → {enriched} new emails | Total: {emails_count}/{len(leads)}")
    return enriched


if __name__ == "__main__":
    start = time.time()
    total = 0
    for f in ["ryzentic_maps.json", "grovitt_maps.json"]:
        total += (run(f, max_leads=200) or 0)
    
    elapsed = time.time() - start
    print(f"\n[✓] {total} leads enriched in {elapsed:.0f}s")
    
    # Final stats
    print("\n=== RESULTS ===")
    grand_total = 0
    for f in ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json", "startup_leads.json"]:
        path = LEADS_DIR / f
        if path.exists():
            with open(path) as fh:
                d = json.load(fh)
            emails = [l for l in d if l.get('contact_email')]
            grand_total += len(d)
            print(f"  {f}: {len(emails)}/{len(d)} have emails")
            if emails:
                for e in emails[:2]:
                    print(f"    {e['company'][:35]} → {e['contact_email']}")
    print(f"  TOTAL: {grand_total} leads")
