#!/usr/bin/env python3
"""
LinkedIn Person Enricher — Find decision-makers via Google dorking.
Uses Scrapling StealthyFetcher to search LinkedIn profiles by company.
Zero cost — no paid APIs.

For each lead (company with website, no people, score >= 3):
  1. Google dork: site:linkedin.com/in/ "COMPANY_NAME" <role keywords>
  2. Extract LinkedIn profile URLs from result links
  3. Parse name from URL slug
  4. Assign role based on the query that found them
"""

import argparse
import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from scrapling import StealthyFetcher

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LEADS_DIR = Path("/mnt/d/leads-folder")
LEADS_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_QUERIES = [
    ('ceo_founder', ['CEO', 'Founder']),
    ('cto_director_vp', ['CTO', 'Director', 'VP']),
    ('head_manager_lead', ['Head', 'Manager', 'Lead']),
]

MIN_DELAY = 3.0
MAX_DELAY = 6.0
RETRIES = 2
RETRY_DELAY = 5.0
MAX_LEADS_PER_RUN = 20
MAX_PEOPLE_PER_COMPANY = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Lead:
    company: str = ""
    website: str = ""
    industry: str = ""
    location: str = ""
    size: str = ""
    signal: str = ""
    signal_detail: str = ""
    contact_email: str = ""
    linkedin: str = ""
    phone: str = ""
    source: str = ""
    score: int = 2
    routing: str = "both"
    notes: str = ""
    search_query: str = ""
    city: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    people: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Name extraction from LinkedIn slug
# ---------------------------------------------------------------------------

def looks_like_id_suffix(part: str) -> bool:
    """Heuristic: does this slug part look like a LinkedIn numeric/alphanumeric ID?"""
    if not part:
        return False
    if part.isdigit():
        return True
    # e.g. 38a75974 — short, high digit ratio, mixed alnum
    if len(part) <= 12:
        digits = sum(1 for c in part if c.isdigit())
        if digits >= 3 and digits / len(part) >= 0.4:
            return True
    return False


def extract_name_from_slug(slug: str) -> str:
    """
    LinkedIn URLs: linkedin.com/in/firstname-lastname-XXXXX
    Extract name parts before the numeric/alphanumeric ID suffix.
    """
    slug = slug.strip().lower().rstrip('/')
    if slug.startswith('/in/'):
        slug = slug[4:]
    elif slug.startswith('in/'):
        slug = slug[3:]

    parts = slug.split('-')
    # Drop trailing parts that look like LinkedIn ID suffixes
    name_parts = []
    for part in parts:
        if looks_like_id_suffix(part):
            break
        if part:
            name_parts.append(part)

    if not name_parts:
        return ""

    # Capitalize each part; single letters are treated as initials (V -> V.)
    capitalized = []
    for p in name_parts:
        if len(p) == 1 and p.isalpha():
            capitalized.append(p.upper() + ".")
        else:
            capitalized.append(p.capitalize())

    return " ".join(capitalized)


def extract_name_from_title(title: str) -> Optional[str]:
    """Try to extract a person's name from a Google result title."""
    if not title:
        return None
    # Titles often look like: "John Smith - CEO at CompanyName | LinkedIn"
    # or "John Smith | LinkedIn"
    title = title.replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
    if " - " in title:
        title = title.split(" - ")[0]
    if " | " in title:
        title = title.split(" | ")[0]
    if " at " in title and len(title.split(" at ")[0].split()) <= 4:
        return title.split(" at ")[0].strip()
    # If it's just 2-3 words, likely a name
    words = title.split()
    if 1 <= len(words) <= 4:
        return title.strip()
    return None


def clean_company_name(name: str) -> str:
    """Clean company name for use in search queries."""
    # Remove common suffixes
    suffixes = [' Pvt.Ltd.', ' Pvt Ltd', ' Pvt. Ltd.', ' Pvt Ltd.', ' Private Limited',
                ' (OPC) Private Limited', ' (OPC) Pvt Ltd', ' Ltd.', ' Limited',
                ' Inc.', ' LLC', ' LLP', ' Corp.', ' Corporation']
    name = name.strip()
    for suffix in suffixes:
        if name.lower().endswith(suffix.lower()):
            name = name[:-len(suffix)].strip()
    # Remove parenthetical content
    name = re.sub(r'\s*\([^)]*\)', '', name)
    return name.strip()


# ---------------------------------------------------------------------------
# Google search + LinkedIn extraction
# ---------------------------------------------------------------------------

def build_google_url(company: str, role_keywords: List[str]) -> str:
    """Build a Google search URL for LinkedIn profiles at a company."""
    clean = clean_company_name(company)
    # Query: site:linkedin.com/in/ "Company Name" "CEO" OR "Founder"
    kw_parts = ' OR '.join(f'"{kw}"' for kw in role_keywords)
    query = f'site:linkedin.com/in/ "{clean}" {kw_parts}'
    return f"https://www.google.com/search?q={urllib.parse.quote(query)}"


def extract_linkedin_urls(response) -> List[Dict[str, str]]:
    """Extract LinkedIn profile URLs and names from Google search results."""
    results = []
    seen = set()

    # Strategy 1: Look for result links with href containing linkedin.com/in/
    links = response.css('a[href]')
    for link in links:
        href = link.attrib.get('href', '')
        text = (link.text or "").strip()
        title = (link.attrib.get('title', '') or link.attrib.get('aria-label', '') or "").strip()

        # Google redirects: /url?q=https://www.linkedin.com/in/...
        direct_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/[^/\s"<>]+', href)
        redirect_match = re.search(r'/url\?q=(https?://(?:www\.)?linkedin\.com/in/[^&\s"<>]+)', href)

        url = None
        if direct_match:
            url = direct_match.group(0)
        elif redirect_match:
            url = urllib.parse.unquote(redirect_match.group(1))

        if not url:
            continue

        # Normalize URL: strip fragment and trailing slash
        url = url.split('#')[0].rstrip('/')
        if url in seen:
            continue
        seen.add(url)

        # Extract slug
        slug_match = re.search(r'/in/([^/?#\s"<>]+)', url)
        if not slug_match:
            continue
        slug = slug_match.group(1)

        # Extract name
        name = extract_name_from_slug(slug)
        # Try title fallback
        if not name or len(name) < 2:
            title_name = extract_name_from_title(title) or extract_name_from_title(text)
            if title_name:
                name = title_name

        if not name or len(name) < 2:
            continue

        results.append({
            "url": url,
            "slug": slug,
            "name": name,
            "title_text": title or text,
        })

    # Strategy 2: Look for h3 titles with nested links (common Google layout)
    h3s = response.css('h3')
    for h3 in h3s:
        h3_text = (h3.get_all_text() or "").strip()
        if not h3_text:
            continue
        # Find nearest parent or sibling link
        parent = h3.parent
        if parent is not None:
            parent_links = parent.css('a[href]')
            for pl in parent_links:
                href = pl.attrib.get('href', '')
                direct_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/[^/\s"<>]+', href)
                redirect_match = re.search(r'/url\?q=(https?://(?:www\.)?linkedin\.com/in/[^&\s"<>]+)', href)
                url = None
                if direct_match:
                    url = direct_match.group(0)
                elif redirect_match:
                    url = urllib.parse.unquote(redirect_match.group(1))
                if url:
                    url = url.split('#')[0].rstrip('/')
                    if url in seen:
                        continue
                    seen.add(url)
                    slug_match = re.search(r'/in/([^/?#\s"<>]+)', url)
                    if slug_match:
                        slug = slug_match.group(1)
                        name = extract_name_from_slug(slug)
                        if not name:
                            name = extract_name_from_title(h3_text) or ""
                        if name and len(name) >= 2:
                            results.append({
                                "url": url,
                                "slug": slug,
                                "name": name,
                                "title_text": h3_text,
                            })

    return results


def infer_role(query_type: str, title_text: str) -> str:
    """Infer the person's role from the query type and title text."""
    title_lower = (title_text or "").lower()

    # Check title text for explicit role mentions
    role_patterns = [
        (r'\bceo\b', 'CEO'),
        (r'\bchief\s+executive\b', 'CEO'),
        (r'\bfounder\b', 'Founder'),
        (r'\bco-founder\b', 'Co-Founder'),
        (r'\bcto\b', 'CTO'),
        (r'\bchief\s+technology\b', 'CTO'),
        (r'\bchief\s+technical\b', 'CTO'),
        (r'\bdirector\b', 'Director'),
        (r'\bvp\b', 'VP'),
        (r'\bvice\s+president\b', 'VP'),
        (r'\bhead\s+of\b', 'Head'),
        (r'\bmanager\b', 'Manager'),
        (r'\blead\b', 'Lead'),
        (r'\bprincipal\b', 'Principal'),
        (r'\bsenior\b', 'Senior'),
    ]

    for pattern, role in role_patterns:
        if re.search(pattern, title_lower):
            return role

    # Fallback to query type
    mapping = {
        'ceo_founder': 'CEO / Founder',
        'cto_director_vp': 'CTO / Director / VP',
        'head_manager_lead': 'Head / Manager / Lead',
    }
    return mapping.get(query_type, 'Executive')


def search_company_people(company: str, website: str) -> List[Dict[str, str]]:
    """Search Google for LinkedIn profiles at a company. Returns up to MAX_PEOPLE_PER_COMPANY people."""
    all_people = []
    seen_urls = set()

    for query_type, role_keywords in SEARCH_QUERIES:
        url = build_google_url(company, role_keywords)
        print(f"    [search] {query_type}: {url[:100]}...")

        for attempt in range(1, RETRIES + 1):
            try:
                ua = random.choice(USER_AGENTS)
                response = StealthyFetcher.fetch(
                    url,
                    headless=True,
                    network_idle=True,
                    wait=3000,
                    retries=1,
                    timeout=45000,
                    block_ads=True,
                    disable_resources=False,
                )

                profiles = extract_linkedin_urls(response)
                print(f"    [found] {len(profiles)} profiles")

                for prof in profiles:
                    if prof["url"] in seen_urls:
                        continue
                    seen_urls.add(prof["url"])

                    role = infer_role(query_type, prof.get("title_text", ""))
                    person = {
                        "name": prof["name"],
                        "role": role,
                        "linkedin": prof["url"],
                        "twitter": "",
                        "github": "",
                    }
                    all_people.append(person)

                    if len(all_people) >= MAX_PEOPLE_PER_COMPANY:
                        return all_people

                break  # success, no retry needed

            except Exception as e:
                print(f"    [attempt {attempt}/{RETRIES}] Error: {e}")
                if attempt < RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
                else:
                    print(f"    Failed after {RETRIES} attempts.")

        # Rate limit delay between searches
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    return all_people


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def load_leads(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_leads(path: Path, leads: List[dict]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def should_process(lead: dict, min_score: int) -> bool:
    """Check if a lead qualifies for LinkedIn enrichment."""
    has_company = bool(lead.get("company", "").strip())
    has_website = bool(lead.get("website", "").strip())
    score = lead.get("score", 0)
    people = lead.get("people", [])
    no_people = not people or len(people) == 0

    return has_company and has_website and score >= min_score and no_people


def process_file(source_path: Path, max_leads: int, min_score: int, dry_run: bool) -> int:
    """Process a single leads file. Returns number of leads enriched."""
    leads = load_leads(source_path)
    if not leads:
        print(f"[!] No leads in {source_path}")
        return 0

    # Filter leads that need enrichment
    candidates = [l for l in leads if should_process(l, min_score)]
    to_process = candidates[:max_leads]

    print(f"[i] {source_path.name}: {len(leads)} total, {len(candidates)} candidates, processing {len(to_process)}")

    if dry_run:
        for lead in to_process:
            print(f"  [dry-run] Would search: {lead.get('company')} ({lead.get('website')})")
        return 0

    enriched_count = 0
    for idx, lead in enumerate(to_process, 1):
        company = lead.get("company", "").strip()
        website = lead.get("website", "").strip()
        print(f"\n  [{idx}/{len(to_process)}] Enriching: {company} ({website})")

        people = search_company_people(company, website)
        if people:
            lead["people"] = people
            enriched_count += 1
            print(f"    [+] Added {len(people)} people")
        else:
            print(f"    [-] No people found")

        # Save after each lead (incremental)
        save_leads(source_path, leads)

    return enriched_count


def main():
    parser = argparse.ArgumentParser(description="LinkedIn Person Enricher — Google dorking for decision-makers")
    parser.add_argument("--source", type=str, default=None, help="Target specific JSON file in leads-folder (e.g. ryzentic_maps.json)")
    parser.add_argument("--max", type=int, default=MAX_LEADS_PER_RUN, help=f"Max leads to process per file (default {MAX_LEADS_PER_RUN})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without running searches")
    parser.add_argument("--min-score", type=int, default=3, help="Minimum lead score to process (default 3)")
    args = parser.parse_args()

    # Determine files to process
    if args.source:
        source_path = LEADS_DIR / args.source
        if not source_path.exists():
            print(f"[!] File not found: {source_path}")
            return
        files = [source_path]
    else:
        files = sorted(LEADS_DIR.glob("*.json"))

    total_enriched = 0
    for file_path in files:
        if file_path.name.endswith("_people.json"):
            continue  # skip output files
        count = process_file(file_path, args.max, args.min_score, args.dry_run)
        total_enriched += count

    print(f"\n[✓] Done. Enriched {total_enriched} leads across {len(files)} file(s).")


if __name__ == "__main__":
    main()
