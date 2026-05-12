#!/usr/bin/env python3
"""
Startup / Company Lead Scraper (Phase 2)
Sources: BetaList, Hacker News Show, GitHub Trending
Zero cost - no paid APIs
"""

import json
import random
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any

from scrapling import StealthyFetcher

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LEADS_DIR = Path("/mnt/d/leads-folder")
LEADS_DIR.mkdir(parents=True, exist_ok=True)

MIN_DELAY = 2.0
MAX_DELAY = 5.0
RETRIES = 3
RETRY_DELAY = 5.0


def random_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ---------------------------------------------------------------------------
# Data model (same as maps scraper)
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
    score: int = 3
    routing: str = "both"
    notes: str = ""
    search_query: str = ""
    city: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Score / routing helpers
# ---------------------------------------------------------------------------

def score_startup(website: str, description: str = "", is_new_launch: bool = True) -> int:
    score = 3
    if is_new_launch:
        score = 4  # newly launched = actively building
    tech_signals = ['ai', 'saas', 'platform', 'api', 'cloud', 'automation',
                    'software', 'app', 'development', 'infrastructure']
    if any(s in (description or "").lower() for s in tech_signals):
        score = min(5, score + 1)
    if not website:
        score = min(5, score + 1)  # No website = desperate need
    return min(5, max(1, score))


def route_startup(description: str, title: str = "") -> str:
    text = f"{description} {title}".lower()
    tech = ['software', 'saas', 'ai', 'platform', 'api', 'cloud', 'dev',
            'automation', 'infrastructure', 'security', 'data', 'app',
            'development', 'engineering', 'tech', 'tool', 'framework',
            'open source', 'github', 'code']
    marketing = ['marketing', 'brand', 'ecommerce', 'retail', 'consumer',
                 'growth', 'ads', 'social', 'content', 'fashion',
                 'design', 'creative', 'media', 'agency']
    has_tech = any(s in text for s in tech)
    has_marketing = any(s in text for s in marketing)
    if has_tech and has_marketing:
        return "both"
    elif has_tech:
        return "ryzentic"
    elif has_marketing:
        return "grovitt"
    return "both"


# ---------------------------------------------------------------------------
# BetaList scraper
# ---------------------------------------------------------------------------

def fetch_betalist(max_items: int = 50) -> List[Lead]:
    """Scrape BetaList for newly launched startups."""
    leads: List[Lead] = []
    url = "https://betalist.com"

    print(f"\n[+] Fetching BetaList: {url}")

    for attempt in range(1, RETRIES + 1):
        try:
            StealthyFetcher.adaptive = True
            response = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                wait=5000,
                retries=1,
                timeout=30000,
                block_ads=True,
                disable_resources=False,
            )

            # Find startup containers
            startup_divs = response.css('div[class*="startup"]')
            print(f"  Found {len(startup_divs)} startup divs")

            seen = set()
            for div in startup_divs[:max_items * 2]:  # overfetch to account for dups
                try:
                    text = div.get_all_text() or ""
                    if not text:
                        continue

                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    if not lines:
                        continue

                    # Name is first line, description is second
                    name = lines[0]
                    description = lines[1] if len(lines) > 1 else ""

                    if not name or len(name) < 2 or name in seen:
                        continue
                    seen.add(name)

                    # Look for link
                    link_els = div.css('a[href]')
                    link_el = link_els[0] if link_els else None
                    website = ""
                    if link_el:
                        href = link_el.attrib.get('href', '')
                        if href.startswith('/'):
                            website = f"https://betalist.com{href}"
                        elif href.startswith('http'):
                            website = href

                    routing = route_startup(description, name)
                    score = score_startup(website, description, is_new_launch=True)

                    lead = Lead(
                        company=name,
                        website=website,
                        industry="",
                        location="",
                        size="1-10",
                        signal="betalist_launch",
                        signal_detail=f"Recently launched on BetaList",
                        contact_email="",
                        linkedin="",
                        phone="",
                        source="betalist",
                        score=score,
                        routing=routing,
                        notes=description,
                    )
                    leads.append(lead)
                except Exception as e:
                    print(f"  [parse warning] {e}")
                    continue

            print(f"  Total BetaList leads: {len(leads)}")
            return leads

        except Exception as e:
            print(f"  [attempt {attempt}/{RETRIES}] Error: {e}")
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                print(f"  Failed after {RETRIES} attempts.")
                return []

    return leads


# ---------------------------------------------------------------------------
# Hacker News Show scraper
# ---------------------------------------------------------------------------

def fetch_hackernews_show(max_items: int = 30) -> List[Lead]:
    """Scrape Hacker News Show HN for project launches."""
    leads: List[Lead] = []
    url = "https://news.ycombinator.com/show"

    print(f"\n[+] Fetching HN Show: {url}")

    for attempt in range(1, RETRIES + 1):
        try:
            StealthyFetcher.adaptive = True
            response = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                wait=3000,
                retries=1,
                timeout=30000,
                block_ads=True,
                disable_resources=False,
            )

            # HN Show uses simple HTML: .titleline > a
            titles = response.css('.titleline > a')
            print(f"  Found {len(titles)} Show HN titles")

            for title in titles[:max_items]:
                try:
                    text = title.text or ""
                    href = title.attrib.get('href', '')

                    if not text or not text.lower().startswith('show hn'):
                        continue

                    # Parse "Show HN: Name - Description" or "Show HN: Name"
                    clean_text = re.sub(r'^Show HN:\s*', '', text, flags=re.IGNORECASE)
                    parts = re.split(r'[–—\-–\|]', clean_text, maxsplit=1)
                    name = parts[0].strip()
                    description = parts[1].strip() if len(parts) > 1 else ""

                    # Website from link
                    website = href if href.startswith('http') else ""

                    routing = route_startup(description, name)
                    score = score_startup(website, description, is_new_launch=True)

                    # Extract source/discussion link (HN thread)
                    parent = title.parent
                    discussion_link = ""
                    if parent:
                        # Find the "discuss" or comments link
                        discuss = parent.css('a[href^="item?id="]')
                        if discuss:
                            discussion_link = f"https://news.ycombinator.com/{discuss[0].attrib.get('href', '')}"

                    lead = Lead(
                        company=name,
                        website=website,
                        industry="",
                        location="",
                        size="1-10",
                        signal="hackernews_show",
                        signal_detail=f"Launched on Hacker News Show",
                        contact_email="",
                        linkedin="",
                        phone="",
                        source="hackernews",
                        score=score,
                        routing=routing,
                        notes=f"{description}\nHN discussion: {discussion_link}",
                    )
                    leads.append(lead)
                except Exception as e:
                    print(f"  [parse warning] {e}")
                    continue

            print(f"  Total HN Show leads: {len(leads)}")
            return leads

        except Exception as e:
            print(f"  [attempt {attempt}/{RETRIES}] Error: {e}")
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                print(f"  Failed after {RETRIES} attempts.")
                return []

    return leads


# ---------------------------------------------------------------------------
# GitHub Trending scraper (tech-focused, good for Ryzentic)
# ---------------------------------------------------------------------------

def fetch_github_trending(language: str = "", period: str = "daily", max_items: int = 25) -> List[Lead]:
    """Scrape GitHub trending repos to find open-source projects/companies."""
    leads: List[Lead] = []
    url = f"https://github.com/trending"
    if language:
        url += f"/{language}"
    url += f"?since={period}"

    print(f"\n[+] Fetching GitHub Trending: {url}")

    for attempt in range(1, RETRIES + 1):
        try:
            StealthyFetcher.adaptive = True
            response = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                wait=4000,
                retries=1,
                timeout=30000,
                block_ads=True,
                disable_resources=False,
            )

            # GitHub trending: article.Box-row
            repos = response.css('article.Box-row')
            print(f"  Found {len(repos)} trending repos")

            for repo in repos[:max_items]:
                try:
                    # Repo name
                    name_els = repo.css('h2 a')
                    name_el = name_els[0] if name_els else None
                    if not name_el:
                        continue

                    full_name = name_el.text.strip()  # e.g. "  owner / repo  "
                    full_name = re.sub(r'\s+', '', full_name)  # "owner/repo"
                    parts = full_name.split('/')
                    if len(parts) < 2:
                        continue

                    owner = parts[0]
                    repo_name = parts[1]

                    # Description
                    desc_els = repo.css('p')
                    desc_el = desc_els[0] if desc_els else None
                    description = desc_el.text.strip() if desc_el else ""

                    # Stars
                    stars_els = repo.css('a[href*="stargazers"]')
                    stars_el = stars_els[0] if stars_els else None
                    stars = stars_el.text.strip() if stars_el else ""

                    # Language
                    lang_els = repo.css('[itemprop="programmingLanguage"]')
                    lang_el = lang_els[0] if lang_els else None
                    language_tag = lang_el.text.strip() if lang_el else ""

                    website = f"https://github.com/{full_name}"

                    # Treat as a company/org lead
                    lead = Lead(
                        company=owner,
                        website=website,
                        industry=language_tag or "Open Source",
                        location="",
                        size="1-10",
                        signal="github_trending",
                        signal_detail=f"Trending on GitHub ({period}) | {stars} stars | Language: {language_tag}",
                        contact_email="",
                        linkedin="",
                        phone="",
                        source="github",
                        score=3,
                        routing="ryzentic",  # trending repos always need tech help
                        notes=f"Repo: {repo_name}\nDescription: {description}\nStars: {stars}",
                    )
                    leads.append(lead)
                except Exception as e:
                    print(f"  [parse warning] {e}")
                    continue

            print(f"  Total GitHub leads: {len(leads)}")
            return leads

        except Exception as e:
            print(f"  [attempt {attempt}/{RETRIES}] Error: {e}")
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                print(f"  Failed after {RETRIES} attempts.")
                return []

    return leads


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_scraper(sources: Optional[List[str]] = None):
    """Run the startup lead scraper."""
    sources = sources or ["betalist", "hackernews", "github"]
    all_leads: List[Lead] = []
    seen_companies: set = set()

    out_file = LEADS_DIR / "startup_leads.json"
    if out_file.exists():
        try:
            with open(out_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
                for raw in existing:
                    seen_companies.add(raw.get("company", "").lower().strip())
                print(f"[i] Loaded {len(existing)} existing leads from {out_file}")
                all_leads = [Lead(**{k: v for k, v in raw.items() if k in Lead.__dataclass_fields__}) for raw in existing]
        except Exception as e:
            print(f"[warn] Could not load existing leads: {e}")

    if "betalist" in sources:
        bl_leads = fetch_betalist()
        for lead in bl_leads:
            key = lead.company.lower().strip()
            if key and key not in seen_companies:
                seen_companies.add(key)
                all_leads.append(lead)
        random_delay()

    if "hackernews" in sources:
        hn_leads = fetch_hackernews_show()
        for lead in hn_leads:
            key = lead.company.lower().strip()
            if key and key not in seen_companies:
                seen_companies.add(key)
                all_leads.append(lead)
        random_delay()

    if "github" in sources:
        gh_leads = fetch_github_trending()
        for lead in gh_leads:
            key = lead.company.lower().strip()
            if key and key not in seen_companies:
                seen_companies.add(key)
                all_leads.append(lead)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in all_leads], f, indent=2, ensure_ascii=False)
    print(f"\n[✓] Saved {len(all_leads)} startup leads to {out_file}")
    return all_leads


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Startup Lead Scraper")
    parser.add_argument("--sources", nargs="+",
                        choices=["betalist", "hackernews", "github", "all"],
                        default=["all"],
                        help="Sources to scrape")
    args = parser.parse_args()

    sources = ["betalist", "hackernews", "github"] if "all" in args.sources else args.sources
    run_scraper(sources)


if __name__ == "__main__":
    main()
