#!/usr/bin/env python3
"""
Phase 2A — GitHub Trending + HN Hiring Scraper (rewritten with verified selectors)
Sources: GitHub trending repos, HN "Who is Hiring" Algolia API
"""

import json, random, re, time, requests
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from scrapling import StealthyFetcher

LEADS_DIR = Path("/mnt/d/leads-folder")
LEADS_DIR.mkdir(parents=True, exist_ok=True)

MIN_DELAY = 2.0
MAX_DELAY = 5.0

@dataclass
class Lead:
    company: str = ""; website: str = ""; industry: str = ""
    location: str = ""; size: str = ""; signal: str = ""
    signal_detail: str = ""; contact_email: str = ""; linkedin: str = ""
    phone: str = ""; source: str = ""; score: int = 3; routing: str = "ryzentic"
    notes: str = ""; city: str = ""; search_query: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    def to_dict(self): return asdict(self)

def random_sleep(): time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

def route_leads(description: str) -> str:
    d = description.lower()
    tech = any(k in d for k in ["api","sdk","cloud","devops","infra","backend","frontend","database","security","framework","platform","cli","tool","engine","saas","ml","ai","automation"])
    market = any(k in d for k in ["marketing","growth","analytics","commerce","brand","content","social","seo","creator","ecommerce"])
    return "ryzentic" if tech else "grovitt" if market else "ryzentic"

def scrape_github_trending(lang="all"):
    leads = []
    url = f"https://github.com/trending/{lang}?since=weekly" if lang != "all" else "https://github.com/trending?since=weekly"
    print(f"[+] GitHub trending/{lang}")
    try:
        StealthyFetcher.adaptive = True
        r = StealthyFetcher.fetch(url, headless=True, network_idle=True, wait=5000, timeout=60000, block_ads=True)
        articles = r.css("article.Box-row")
        print(f"  {len(articles)} repos found")
        for a in articles[:10]:
            try:
                links = a.css("h2 a")
                if not links: continue
                href = links[0].attrib.get("href","")
                parts = href.strip("/").split("/")
                if len(parts) < 2: continue
                org, repo = parts[0], parts[1]
                
                # Description
                desc = ""
                for p in a.css("p"):
                    t = p.text.strip()
                    if len(t) > 5: desc = t; break
                
                # Language
                lang_text = ""
                for s in a.css("span"):
                    t = s.text.strip()
                    if t and t not in ["Star","Fork","","Sponsored"] and " " not in t and not t.startswith(","):
                        lang_text = t; break
                
                site = f"https://{org.lower()}.com"
                routing = route_leads(f"{repo} {desc}")
                
                lead = Lead(
                    company=org, website=site, industry=f"Open Source / {lang_text}", location="",
                    signal="github_trending", signal_detail=f"GitHub Trending ({lang}): {repo}",
                    source="github_trending", score=4, routing=routing, city="Global",
                    search_query=f"github_trending_{lang}",
                    notes=f"Repo: {repo} - {desc[:150]}. Language: {lang_text}"
                )
                leads.append(lead)
            except Exception as e:
                continue
        print(f"  → {len(leads)} leads")
        return leads
    except Exception as e:
        print(f"  [err] {e}")
        return []

def scrape_hn_hiring():
    leads = []
    print("[+] HN Who is Hiring (May 2026)")
    thread_id = "47975571"
    try:
        # Fetch comments via HN Algolia API
        url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{thread_id}&hitsPerPage=100&page=0"
        resp = requests.get(url, timeout=30)
        data = resp.json()
        comments = data.get("hits", [])
        print(f"  {len(comments)} comments returned")
        
        seen = set()
        for comment in comments[:50]:
            text = comment.get("comment_text", "")
            if not text or len(text) < 80: continue
            
            # Extract company name (first **bold** text or first line with hiring keywords)
            hiring = ["hiring", "join us", "looking for", "open positions", "careers", "apply", "we're building"]
            if not any(h in text.lower() for h in hiring): continue
            
            # Extract website
            web = ""
            m = re.search(r'(https?://[^\s<>"]+)', text)
            if m: web = m.group(0)
            
            # Company name: first bold text or first capitalized phrase
            company = ""
            m = re.search(r'\*\*([^*]+)\*\*', text)
            if m: company = m.group(1).strip()[:80]
            else:
                for line in text.split('\n')[:3]:
                    line = line.strip()
                    if len(line) > 3 and len(line) < 80 and not line.startswith(('http','-','*',']')):
                        company = line[:80]
                        break
            
            if not company: continue
            if company.lower() in seen: continue
            seen.add(company.lower())
            
            # Location
            loc = ""
            for p in ["remote","hybrid","onsite"]:
                m = re.search(rf'\b{p}\b\s*(.+)', text.lower())
                if m: loc = m.group(1).strip()[:60]; break
            
            lead = Lead(
                company=company, website=web, industry="Tech", location=loc,
                signal="actively_hiring", signal_detail=f"HN Who is Hiring: May 2026",
                source="hn_hiring", score=4, routing="ryzentic", city="Global",
                search_query="hn_who_is_hiring",
                notes=text[:200].replace('\n',' ')
            )
            leads.append(lead)
        
        print(f"  → {len(leads)} leads")
        return leads
    except Exception as e:
        print(f"  [err] {e}")
        return []

def run():
    out = LEADS_DIR / "company_tech_signals.json"
    all_leads = []; seen = set()
    if out.exists():
        with open(out) as f: existing = json.load(f)
        for e in existing: seen.add(e.get("company","").lower().strip())

    for lang in ["all","TypeScript","Python","Go","Rust"]:
        for ld in scrape_github_trending(lang):
            k = ld.company.lower()
            if k not in seen: seen.add(k); all_leads.append(ld)
        random_sleep()

    for ld in scrape_hn_hiring():
        k = ld.company.lower()
        if k not in seen: seen.add(k); all_leads.append(ld)

    with open(out, "w") as f:
        json.dump([l.to_dict() for l in all_leads], f, indent=2, ensure_ascii=False)
    print(f"\n[✓] {len(all_leads)} tech signal leads → {out}")

if __name__ == "__main__":
    run()
