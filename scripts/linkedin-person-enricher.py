#!/usr/bin/env python3
"""
LinkedIn Person Enricher v2 — Multi-engine search rotation.
Uses 5 search engines to find LinkedIn profiles per company.
Rotates engines to avoid rate limits. No API keys required.

Search engines (tried in order):
  Google → DuckDuckGo → Bing → Brave → Startpage

For each lead (company with website, no person emails, score >= 3):
  1. Search: "site:linkedin.com/in/ 'COMPANY_NAME' CEO OR Founder"
  2. Extract LinkedIn URLs + names from results
  3. Rotate to next engine when blocked
"""

import argparse, json, random, re, time, urllib.parse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from scrapling import StealthyFetcher

LEADS_DIR = Path("/mnt/d/leads-folder")
DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]

SEARCH_QUERIES = [
    ("ceo_founder", ["CEO", "Founder"]),
    ("cto_director_vp", ["CTO", "Director", "VP"]),
    ("head_manager_lead", ["Head", "Manager", "Lead"]),
]

# Multi-engine URL templates (in rotation order)
SEARCH_ENGINES = [
    {"name": "Google", "url": "https://www.google.com/search?q={query}"},
    {"name": "DuckDuckGo", "url": "https://duckduckgo.com/html/?q={query}"},
    {"name": "Bing", "url": "https://www.bing.com/search?q={query}"},
    {"name": "Brave", "url": "https://search.brave.com/search?q={query}"},
    {"name": "Startpage", "url": "https://www.startpage.com/sp/search?query={query}"},
]

MIN_DELAY, MAX_DELAY = 3.0, 6.0
MAX_LEADS_PER_RUN = 20
MAX_PEOPLE = 5
RETRIES = 2
RETRY_DELAY = 5.0
ENGINE_COOLDOWN = 30  # seconds to cool down a blocked engine

# Track which engine is blocked and until when
_blocked_until: Dict[str, float] = {}

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
        for k, v in self._extra.items(): d[k] = v
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "Lead":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kw, ex = {}, {}
        for k, v in raw.items(): (kw if k in known else ex)[k] = v
        l = cls(**kw); l._extra = ex; return l

# ─── Name extraction ────────────────────────────────────────────

def looks_like_id_suffix(part: str) -> bool:
    if not part: return False
    if part.isdigit(): return True
    if len(part) <= 12:
        digits = sum(1 for c in part if c.isdigit())
        if digits >= 3 and digits / len(part) >= 0.4: return True
    return False

def extract_name_from_slug(slug: str) -> str:
    slug = slug.strip().lower().rstrip('/')
    if slug.startswith('/in/'): slug = slug[4:]
    elif slug.startswith('in/'): slug = slug[3:]
    parts = slug.split('-')
    name_parts = []
    for part in parts:
        if looks_like_id_suffix(part): break
        if part: name_parts.append(part)
    if not name_parts: return ""
    capitalized = []
    for p in name_parts:
        if len(p) == 1 and p.isalpha(): capitalized.append(p.upper() + ".")
        else: capitalized.append(p.capitalize())
    return " ".join(capitalized)

def clean_company(name: str) -> str:
    name = name.strip()
    for s in ['Pvt.Ltd.','Pvt Ltd','Pvt. Ltd.','Private Limited','(OPC) Private Limited','Ltd.','Limited','Inc.','LLC','LLP','Corp.']:
        if name.lower().endswith(s.lower()): name = name[:-len(s)].strip()
    name = re.sub(r'\s*\([^)]*\)', '', name)
    return name.strip()

# ─── Multi-engine search ───────────────────────────────────────

def get_available_engine() -> Optional[dict]:
    """Return the first non-blocked search engine."""
    now = time.time()
    for eng in SEARCH_ENGINES:
        until = _blocked_until.get(eng["name"], 0)
        if now >= until:
            return eng
    return None

def block_engine(name: str):
    """Mark an engine as blocked for cooldown period."""
    _blocked_until[name] = time.time() + ENGINE_COOLDOWN
    print(f"    🚫 Blocked {name} for {ENGINE_COOLDOWN}s")

def build_query(company: str, keywords: List[str]) -> str:
    clean = clean_company(company)
    kws = ' OR '.join(f'"{kw}"' for kw in keywords)
    return f'site:linkedin.com/in/ "{clean}" {kws}'

def extract_linkedin_from_page(response) -> List[Dict[str, str]]:
    """Extract LinkedIn URLs and names from any search engine result page."""
    results = []
    seen = set()

    try:
        all_text = response.get_all_text() or ""
    except:
        all_text = ""

    # Universal LinkedIn URL extraction from rendered text
    li_pattern = re.findall(
        r'(?:https?://)?(?:www\.)?linkedin\.com/in/([a-zA-Z0-9._%-]+)(?:/[?#][^\s"<>]*)?',
        all_text, re.I
    )

    for slug in li_pattern:
        slug = re.sub(r'[-.]$', '', slug)  # clean trailing dots/dashes
        if slug in seen: continue
        seen.add(slug)

        url = f"https://www.linkedin.com/in/{slug}"
        name = extract_name_from_slug(slug)
        if name and len(name) >= 2:
            results.append({"url": url, "slug": slug, "name": name})

    # Also try CSS extraction for structured results
    try:
        for a_tag in response.css('a[href*="linkedin.com/in/"]'):
            href = a_tag.attrib.get('href', '')
            # Handle Google redirect format
            redirect = re.search(r'/url\?q=(https?://[^&\s]+)', href)
            if redirect:
                href = urllib.parse.unquote(redirect.group(1))

            slug_match = re.search(r'/in/([^/?#\s]+)', href)
            if not slug_match: continue
            slug = slug_match.group(1)
            if slug in seen: continue
            seen.add(slug)

            name = extract_name_from_slug(slug)
            if name and len(name) >= 2:
                results.append({
                    "url": f"https://www.linkedin.com/in/{slug}",
                    "slug": slug,
                    "name": name,
                })
    except:
        pass

    return results[:MAX_PEOPLE * 3]

def search_linkedin(company: str, keywords: List[str]) -> List[Dict]:
    """Search for LinkedIn profiles across all available engines."""
    query = build_query(company, keywords)
    all_results = []

    for attempt in range(len(SEARCH_ENGINES)):
        engine = get_available_engine()
        if not engine:
            print(f"    ❌ All search engines blocked!")
            break

        search_url = engine["url"].format(query=urllib.parse.quote(query))
        eng_name = engine["name"]

        try:
            StealthyFetcher.adaptive = True
            page = StealthyFetcher.fetch(
                search_url,
                headless=True,
                network_idle=True,
                wait=3000,
                timeout=20000
            )
            results = extract_linkedin_from_page(page)

            if results:
                print(f"    ✅ {eng_name}: {len(results)} profiles")
                all_results.extend(results)
                return all_results

            # Check if we got blocked
            text = page.get_all_text() or ""
            if any(b in text.lower() for b in ['captcha', 'unusual traffic', 'sorry', '429', 'blocked']):
                block_engine(eng_name)
            else:
                # No results but not blocked — engine just has no data
                print(f"    ⚪ {eng_name}: no profiles found")

        except Exception as e:
            print(f"    ⚠️ {eng_name}: {str(e)[:80]}")
            block_engine(eng_name)

        time.sleep(1)

    return all_results

# ─── Enrich ────────────────────────────────────────────────────

def enrich_lead(lead: Lead) -> int:
    new = 0
    all_people: Dict[str, Dict] = {}  # deduplicate by URL

    for query_type, keywords in SEARCH_QUERIES:
        results = search_linkedin(lead.company, keywords)
        for r in results:
            url = r["url"]
            if url in all_people: continue
            all_people[url] = {
                "name": r["name"],
                "role": query_type.replace("_", " / ").upper(),
                "linkedin": url,
                "twitter": "",
                "github": "",
                "email": "",
            }

        if len(all_people) >= MAX_PEOPLE:
            break

    # Merge with existing people list
    existing_urls = {p.get("linkedin","") for p in lead.people if isinstance(p, dict)}
    for url, person in all_people.items():
        if url not in existing_urls:
            lead.people.append(person)
            new += 1

    return new

# ─── Batch ────────────────────────────────────────────────────

def process_file(fp: Path, max_l: int, dry: bool, ms: int) -> int:
    with open(fp, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not raw: print(f"  {fp.name}: empty"); return 0

    leads = [Lead.from_dict(r) for r in raw]
    candidates = [l for l in leads if l.website and l.score >= ms]

    # Filter: leads that don't have person emails yet
    need = [
        l for l in candidates
        if not any(
            (p.get("email") if isinstance(p, dict) else getattr(p, "email", None))
            for p in (l.people or [])
        )
    ][:max_l]

    print(f"\n📄 {fp.name}: {len(need)}/{len(leads)} need LinkedIn enrichment")
    print(f"   Search engines: {', '.join(e['name'] for e in SEARCH_ENGINES)}")

    total = 0
    for i, lead in enumerate(need, 1):
        nm = lead.company[:45] if lead.company else "?"
        print(f"\n  [{i}/{len(need)}] {nm}")

        try:
            n = enrich_lead(lead)
            total += n
        except Exception as e:
            print(f"    ❌ {e}")

        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    if not dry and total > 0:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
        print(f"\n  💾 Saved ({total} new profiles)")

    people = len([l for l in leads if l.people])
    print(f"  📊 Leads with people: {people}/{len(leads)}")
    return total

def main():
    p = argparse.ArgumentParser(description="LinkedIn Enricher v2 — Multi-engine")
    p.add_argument("--source", default=None)
    p.add_argument("--max", type=int, default=MAX_LEADS_PER_RUN)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-score", type=int, default=MIN_SCORE)
    args = p.parse_args()

    files = [LEADS_DIR / args.source] if args.source else [LEADS_DIR / f for f in DEFAULT_FILES]
    files = [f for f in files if f.exists()]
    if not files: print("No files."); return

    s = time.time(); t = 0
    for fp in files: t += process_file(fp, args.max, args.dry_run, args.min_score)
    print(f"\n✅ {t} profiles in {time.time()-s:.0f}s")

if __name__ == "__main__": main()
