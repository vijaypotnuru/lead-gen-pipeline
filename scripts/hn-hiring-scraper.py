#!/usr/bin/env python3
"""
Phase 2B — HN "Who is Hiring" Scraper using Algolia API
Extracts companies actively hiring from the latest monthly thread.
"""

import json, re, time, requests
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

LEADS_DIR = Path("/mnt/d/leads-folder")
LEADS_DIR.mkdir(parents=True, exist_ok=True)

@dataclass
class Lead:
    company: str = ""; website: str = ""; industry: str = ""
    location: str = ""; size: str = ""; signal: str = ""
    signal_detail: str = ""; contact_email: str = ""; linkedin: str = ""
    phone: str = ""; source: str = ""; score: int = 4; routing: str = "ryzentic"
    notes: str = ""; city: str = ""; search_query: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    def to_dict(self): return asdict(self)

def scrape_hn_hiring_algolia(thread_id="47975571"):
    """Scrape HN hiring comments via Algolia API (free, no auth needed)."""
    leads = []
    print(f"[+] HN Who is Hiring via Algolia (thread {thread_id})")
    try:
        url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{thread_id}&hitsPerPage=100&page=0"
        resp = requests.get(url, timeout=30)
        data = resp.json()
        comments = data.get("hits", [])
        print(f"  {len(comments)} comments")

        hiring_keywords = ["hiring", "join us", "looking for", "open positions",
                          "careers", "apply", "we're building", "we are hiring"]

        seen = set()
        for comment in comments[:80]:
            text = comment.get("comment_text", "")
            if not text or len(text) < 80:
                continue
            if not any(h in text.lower() for h in hiring_keywords):
                continue

            # Extract website
            web = ""
            m = re.search(r'(https?://[^\s<>"]+)', text)
            if m:
                web = m.group(0)

            # Company name
            company = ""
            m = re.search(r'\*\*([^*]+)\*\*', text)
            if m:
                company = m.group(1).strip()[:80]
            else:
                for line in text.split('\n')[:3]:
                    line = line.strip()
                    if 3 < len(line) < 80 and not line.startswith(('http', '-', '*', ']', '>')):
                        company = line[:80]
                        break
            if not company:
                continue
            if company.lower() in seen:
                continue
            seen.add(company.lower())

            # Location
            loc = ""
            for p in ["remote", "hybrid", "onsite"]:
                m = re.search(rf'\b{p}\b\s*(.{{3,60}})', text, re.I)
                if m:
                    loc = m.group(1).strip()[:60]
                    break

            lead = Lead(
                company=company, website=web, industry="Tech", location=loc,
                signal="actively_hiring", signal_detail="HN Who is Hiring: May 2026",
                source="hn_hiring", score=4, routing="ryzentic", city="Global",
                search_query="hn_who_is_hiring_2026_05", notes=text[:200].replace('\n', ' ')
            )
            leads.append(lead)

        print(f"  → {len(leads)} leads")
        return leads
    except Exception as e:
        print(f"  [err] {e}")
        return []


def find_latest_thread():
    """Find the latest 'Who is hiring' thread ID using Algolia."""
    try:
        url = "https://hn.algolia.com/api/v1/search?query=who+is+hiring+2026&tags=story&hitsPerPage=3&numericFilters=created_at_i>1700000000"
        resp = requests.get(url, timeout=15)
        data = resp.json()
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            if "who is hiring" in title.lower():
                return hit.get("objectID", "")
    except Exception:
        pass
    return None


def run():
    out_file = LEADS_DIR / "hn_hiring_leads.json"
    all_leads = []
    seen = set()

    if out_file.exists():
        try:
            with open(out_file) as f:
                all_leads = [Lead(**{k: v for k, v in raw.items() if k in Lead.__dataclass_fields__}) for raw in json.load(f)]
                for l in all_leads:
                    seen.add(l.company.lower().strip())
            print(f"[i] Loaded {len(all_leads)} existing leads")
        except Exception:
            pass

    thread_id = find_latest_thread()
    if not thread_id:
        thread_id = "47975571"  # May 2026
        print(f"[i] Using known thread: {thread_id}")
    else:
        print(f"[i] Found thread: {thread_id}")

    for lead in scrape_hn_hiring_algolia(thread_id):
        key = lead.company.lower().strip()
        if key and key not in seen:
            seen.add(key)
            all_leads.append(lead)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in all_leads], f, indent=2, ensure_ascii=False)
    print(f"[✓] {len(all_leads)} HN hiring leads → {out_file}")


if __name__ == "__main__":
    run()
