#!/usr/bin/env python3
"""
Google Maps Lead Scraper for Ryzentic and Grovitt
Uses Scrapling StealthyFetcher with headless Playwright
Zero cost - no paid APIs

Google Maps result card text format (newline-separated):
  Business Name
  Rating
  Category
  ·
  
  ·
  Address
  Status
  ·
  Phone
  
  Website
  
  Directions
"""

import json
import random
import re
import time
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

SEARCH_CONFIG: Dict[str, Dict[str, Any]] = {
    "ryzentic": {
        "routing": "ryzentic",
        "queries": [
            ("software development company", ["New York", "London", "Singapore", "Dubai", "Sydney"]),
            ("IT services company", ["Los Angeles", "Toronto", "Berlin", "Mumbai", "Sydney"]),
            ("cloud consulting firm", ["San Francisco", "London", "Dubai", "Singapore", "Sydney"]),
            ("cybersecurity company", ["New York", "London", "Tel Aviv", "Singapore", "Dubai"]),
            ("digital transformation agency", ["London", "New York", "Dubai", "Singapore", "Sydney"]),
            ("automation solutions", ["San Francisco", "London", "Berlin", "Toronto", "Dubai"]),
            ("restaurant", ["New York", "London", "Paris", "Dubai", "Singapore"]),
            ("hotel", ["Dubai", "London", "Paris", "New York", "Singapore"]),
            ("dental clinic", ["London", "New York", "Toronto", "Sydney", "Dubai"]),
            ("law firm", ["New York", "London", "Dubai", "Singapore", "Toronto"]),
            ("real estate agency", ["Dubai", "London", "New York", "Miami", "Sydney"]),
        ],
    },
    "grovitt": {
        "routing": "grovitt",
        "queries": [
            ("marketing agency", ["London", "New York", "Los Angeles", "Dubai", "Singapore"]),
            ("branding agency", ["London", "New York", "Paris", "Dubai", "Sydney"]),
            ("ecommerce business", ["London", "New York", "Dubai", "Singapore", "Berlin"]),
            ("DTC brand", ["London", "New York", "Los Angeles", "Dubai", "Toronto"]),
            ("growth marketing agency", ["London", "San Francisco", "New York", "Dubai", "Singapore"]),
            ("performance marketing", ["London", "New York", "Dubai", "Singapore", "Sydney"]),
            ("restaurant", ["London", "New York", "Dubai", "Paris", "Singapore"]),
            ("fashion boutique", ["London", "Paris", "New York", "Dubai", "Milan"]),
            ("startup incubator", ["London", "San Francisco", "New York", "Berlin", "Dubai"]),
            ("SaaS company", ["San Francisco", "London", "New York", "Dubai", "Singapore"]),
        ],
    },
}

MAX_RESULTS_PER_QUERY = 20
MAX_SCROLLS = 3
MIN_DELAY = 2.0
MAX_DELAY = 5.0
RETRIES = 3
RETRY_DELAY = 5.0


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
    signal: str = "maps_search"
    signal_detail: str = ""
    contact_email: str = ""
    linkedin: str = ""
    phone: str = ""
    source: str = "google_maps"
    score: int = 2
    routing: str = "both"
    notes: str = ""
    search_query: str = ""
    city: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def random_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ---------------------------------------------------------------------------
# Card text parser
# ---------------------------------------------------------------------------

def clean_card_text(text: str) -> str:
    """Remove private-use-area Unicode icons from card text."""
    # Remove PUA characters:  (U+E934),  (U+E80B),  (U+E52E), , , · etc.
    # These are Google's custom icons
    text = re.sub(r'[\ue000-\uf8ff]', '', text)  # Private Use Area
    text = text.replace('·', '').replace('\u2022', '')  # Middle dot / bullet
    text = re.sub(r'\n+', '\n', text)  # collapse multiple newlines
    return text.strip()


def looks_like_rating(text: str) -> bool:
    """Check if text is a star rating like '4.5' or '5.0'."""
    text = text.strip()
    if re.match(r'^\d(\.\d)?$', text):
        try:
            val = float(text)
            return 1.0 <= val <= 5.0
        except ValueError:
            return False
    return False


def looks_like_phone(text: str) -> bool:
    """Check if text looks like a phone number."""
    text = text.strip()
    # Starts with + and has 7+ digits
    if text.startswith('+') and len(re.sub(r'\D', '', text)) >= 7:
        return True
    # US format like (212) 555-0123
    if re.match(r'^\(\d{3}\)\s*\d{3}[-\s]?\d{4}$', text):
        return True
    # Generic: has 10+ digits
    digits = re.sub(r'\D', '', text)
    if len(digits) >= 10 and any(c in text for c in ['+', '(', '-', ' ']):
        return True
    return False


def looks_like_address(text: str) -> bool:
    """Heuristic: does this look like a street address?"""
    text = text.strip()
    if len(text) < 5 or len(text) > 120:
        return False
    # Must have at least one digit (house number)
    if not any(c.isdigit() for c in text):
        return False
    # Common street indicators
    indicators = ['st', 'ave', 'rd', 'dr', 'blvd', 'ln', 'way', 'ct', 'cir', 'pl',
                  'street', 'avenue', 'road', 'drive', 'lane', 'suite', 'floor',
                  'building', 'tower', 'plaza', 'center', 'centre', 'park', 'gardens',
                  'fulton', 'lexington', 'madison', 'broadway', 'wall', 'columbus',
                  'howard', 'newkirk', 'delancey', 'broad', 'franklin', 'main',
                  'market', 'elm', 'oak', 'pine', 'maple', 'cedar', 'spruce']
    text_lower = text.lower()
    return any(ind in text_lower for ind in indicators)


def looks_like_status(text: str) -> bool:
    """Business status like 'Open 24 hours', 'Closed', 'Opens 9 am'."""
    text_lower = text.lower().strip()
    keywords = ['open', 'closed', 'opens', 'closes', '24 hours', 'open now',
                'opens soon', 'closing soon', 'permanently closed']
    return any(kw in text_lower for kw in keywords)


def looks_like_directions(text: str) -> bool:
    return text.strip().lower() == 'directions'


def looks_like_website_label(text: str) -> bool:
    return text.strip().lower() == 'website'


def looks_like_category(text: str, query: str) -> bool:
    """Heuristic: is this a business category label?"""
    text = text.strip()
    if len(text) < 3 or len(text) > 50:
        return False
    if looks_like_rating(text) or looks_like_phone(text) or looks_like_address(text) or looks_like_status(text):
        return False
    if looks_like_directions(text) or looks_like_website_label(text):
        return False
    # Common category words
    category_words = ['company', 'agency', 'restaurant', 'hotel', 'clinic',
                      'firm', 'business', 'store', 'shop', 'service', 'studio',
                      'consulting', 'solutions', 'technology', 'software', 'marketing',
                      'brand', 'design', 'development', 'group', 'inc', 'llc', 'ltd']
    text_lower = text.lower()
    return any(w in text_lower for w in category_words) or text_lower == text_lower  # accept if nothing else matches


def parse_card_text(raw_text: str, query: str) -> Dict[str, Any]:
    """Parse a Google Maps result card's newline-separated text into structured fields."""
    text = clean_card_text(raw_text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    result: Dict[str, Any] = {
        "name": "",
        "rating": "",
        "category": "",
        "address": "",
        "status": "",
        "phone": "",
        "has_website": False,
        "raw_lines": lines,
    }

    if not lines:
        return result

    # Name is the first line (always)
    result["name"] = lines[0]

    # Categorize remaining lines
    i = 1
    while i < len(lines):
        line = lines[i]

        if looks_like_rating(line):
            result["rating"] = line
        elif looks_like_phone(line):
            result["phone"] = line
        elif looks_like_address(line):
            result["address"] = line
        elif looks_like_status(line):
            result["status"] = line
        elif looks_like_website_label(line):
            result["has_website"] = True
        elif looks_like_directions(line):
            pass  # skip
        elif looks_like_category(line, query) and not result["category"]:
            result["category"] = line
        else:
            # Unknown line - could be extended name, second address line, etc.
            if not result["address"] and any(c.isdigit() for c in line) and len(line) > 8:
                result["address"] = line
            elif not result["category"] and len(line) > 3:
                result["category"] = line

        i += 1

    return result


def extract_website_from_card(card) -> str:
    """Extract actual website URL from a result card element."""
    links = card.css('a[href]')
    for link in links:
        href = link.attrib.get('href', '')
        text = (link.text or "").strip().lower()
        if href.startswith('http') and 'google.com' not in href and 'maps' not in href:
            return href
    return ""


def score_lead(routing: str, query: str, has_website: bool, rating: str) -> int:
    score = 2

    tech_keywords = ["software", "it services", "cloud", "cybersecurity",
                     "digital transformation", "automation", "saas", "startup"]
    marketing_keywords = ["marketing", "branding", "ecommerce", "dtc",
                            "growth", "performance"]

    if routing == "ryzentic":
        if any(kw in query.lower() for kw in tech_keywords):
            score = 3
        if not has_website:
            score = 4
    elif routing == "grovitt":
        if any(kw in query.lower() for kw in marketing_keywords):
            score = 3
        if "restaurant" in query.lower() or "hotel" in query.lower():
            score = 3

    try:
        if rating and float(rating) >= 4.0:
            score = min(5, score + 1)
    except (ValueError, TypeError):
        pass

    return min(5, max(1, score))


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scroll_page(page, scrolls: int = MAX_SCROLLS):
    for i in range(scrolls):
        try:
            page.evaluate("""
                (() => {
                    const scrollables = Array.from(document.querySelectorAll('div[role="main"] div'));
                    const container = scrollables.find(el => el.scrollHeight > el.clientHeight + 100);
                    if (container) {
                        container.scrollBy(0, 800);
                        return 'scrolled';
                    }
                    window.scrollBy(0, 800);
                    return 'window-scrolled';
                })()
            """)
        except Exception as e:
            print(f"  [scroll warning] {e}")
        time.sleep(random.uniform(1.5, 3.0))


def parse_maps_page(response, query: str, city: str, routing: str) -> List[Lead]:
    leads: List[Lead] = []

    # Primary strategy: role="article" elements
    cards = response.css('[role="article"]')

    if not cards:
        # Fallback: parent divs of place links
        place_links = response.css('a[href*="/maps/place/"]')
        seen_parents = set()
        for link in place_links:
            parent = link.parent
            if parent is not None:
                pid = id(parent)
                if pid not in seen_parents:
                    seen_parents.add(pid)
                    cards.append(parent)

    print(f"  Found {len(cards)} result cards")

    for card in cards[:MAX_RESULTS_PER_QUERY]:
        try:
            card_text = card.get_all_text() or ""
            if not card_text:
                continue

            parsed = parse_card_text(card_text, query)
            name = parsed["name"]
            if not name or len(name) < 2:
                continue

            # Skip non-business entries
            skip_names = ('google', 'your location', 'add a missing place',
                          'add your business', 'advertising', 'promoted',
                          'sponsored', 'claim this business')
            if name.lower() in skip_names or any(s in name.lower() for s in skip_names):
                continue

            website = extract_website_from_card(card)
            has_website = bool(website) or parsed["has_website"]

            industry = parsed["category"] or query.split(" in ")[0] if " in " in query else query
            location = f"{parsed['address']}, {city}" if parsed["address"] else city

            final_routing = routing
            if not has_website and ("restaurant" in query.lower() or "hotel" in query.lower() or
                                    "dental" in query.lower() or "law firm" in query.lower() or
                                    "real estate" in query.lower()):
                final_routing = "both"

            score = score_lead(routing, query, has_website, parsed["rating"])

            signal_detail = f"Google Maps: '{query}' in {city}"
            if parsed["rating"]:
                signal_detail += f" | Rating: {parsed['rating']}"
            if parsed["phone"]:
                signal_detail += f" | Phone: {parsed['phone']}"
            if not has_website:
                signal_detail += " | No website detected"

            lead = Lead(
                company=name,
                website=website,
                industry=industry,
                location=location,
                size="",
                signal="maps_search",
                signal_detail=signal_detail,
                contact_email="",
                linkedin="",
                phone=parsed["phone"],
                source="google_maps",
                score=score,
                routing=final_routing,
                notes=f"Found via Google Maps search for '{query}' in {city}. "
                      f"Address: {parsed['address'] or 'N/A'}. "
                      f"Category: {parsed['category'] or 'N/A'}",
                search_query=query,
                city=city,
            )
            leads.append(lead)
        except Exception as e:
            print(f"  [parse warning] {e}")
            continue

    return leads


def fetch_maps_page(url: str, query: str, city: str, routing: str) -> List[Lead]:
    print(f"\n[+] Fetching: {url}")

    for attempt in range(1, RETRIES + 1):
        try:
            StealthyFetcher.adaptive = True

            response = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                wait=4000,
                retries=1,
                timeout=50000,
                block_ads=True,
                disable_resources=False,
                page_action=lambda page: scroll_page(page, MAX_SCROLLS),
            )

            leads = parse_maps_page(response, query, city, routing)
            print(f"  Extracted {len(leads)} leads")
            return leads

        except Exception as e:
            print(f"  [attempt {attempt}/{RETRIES}] Error: {e}")
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                print(f"  Failed after {RETRIES} attempts.")
                return []

    return []


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_scraper(company_key: str, max_queries: Optional[int] = None,
                max_cities_per_query: Optional[int] = None):
    config = SEARCH_CONFIG[company_key]
    routing = config["routing"]
    all_queries = config["queries"]

    if max_queries:
        all_queries = all_queries[:max_queries]

    all_leads: List[Lead] = []
    seen_companies: set = set()

    out_file = LEADS_DIR / f"{company_key}_maps.json"
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

    for query, cities in all_queries:
        cities_to_run = cities
        if max_cities_per_query:
            cities_to_run = cities[:max_cities_per_query]

        for city in cities_to_run:
            url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}+in+{city.replace(' ', '+')}"
            leads = fetch_maps_page(url, query, city, routing)

            new_leads = []
            for lead in leads:
                key = lead.company.lower().strip()
                if key and key not in seen_companies:
                    seen_companies.add(key)
                    new_leads.append(lead)

            all_leads.extend(new_leads)
            print(f"  New unique leads: {len(new_leads)} (total: {len(all_leads)})")

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump([l.to_dict() for l in all_leads], f, indent=2, ensure_ascii=False)
            print(f"  Saved to {out_file}")

            random_delay()

    print(f"\n[✓] Finished {company_key}. Total leads: {len(all_leads)}")
    return all_leads


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Google Maps Lead Scraper")
    parser.add_argument("--company", choices=["ryzentic", "grovitt", "both"], default="both")
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--max-cities", type=int, default=None)
    args = parser.parse_args()

    companies = ["ryzentic", "grovitt"] if args.company == "both" else [args.company]

    for company in companies:
        run_scraper(company, max_queries=args.max_queries, max_cities_per_query=args.max_cities)

    print("\n[✓] All done.")


if __name__ == "__main__":
    main()
