#!/usr/bin/env python3
"""Quick test scraper: 10 leads from Google Maps Hyderabad. Uses same engine as main scraper."""

import json, os, sys, time, random, re
from datetime import datetime, timezone
from typing import Dict, List
from scrapling import StealthyFetcher

QUERIES = [
    "software development company",
    "IT services company",
    "digital marketing agency",
    "web development company",
    "cloud consulting",
]
CITY = "Hyderabad"
OUTPUT_DIR = "/mnt/d/leads-folder"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "hyderabad_test_leads.json")
MAX_PER_QUERY = 5
TARGET_TOTAL = 10

def parse_card_text(raw_text: str, query: str) -> Dict[str, str]:
    """Extract name, rating, phone, category from card text."""
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    result = {"name": "", "rating": "", "phone": "", "category": "", "address": ""}
    
    if lines:
        result["name"] = lines[0]
    
    for line in lines:
        # Rating: 4.5 (123) or just a number like 4.7
        rm = re.search(r'(\d\.\d)\s*\(\d+[^)]*\)?\s*', line)
        if not rm:
            rm = re.match(r'^(\d\.\d)$', line)
        if rm:
            result["rating"] = rm.group(1)
        
        # Phone
        if re.search(r'[\d\s\-()+]{7,20}', line) and not result["phone"]:
            result["phone"] = line
        
        # Category
        if any(kw in line.lower() for kw in ['company', 'agency', 'service', 'consult', 'develop', 'firm', 'solution']):
            result["category"] = line
    
    # Address: look for city/area mentions
    for line in lines:
        if any(c in line for c in ['Hyderabad', 'HITEC City', 'Madhapur', 'Gachibowli', 'Banjara Hills', 'Jubilee Hills', 'Kondapur', 'Secunderabad']):
            result["address"] = line
            break
    
    return result

def extract_website_from_card(card) -> str:
    links = card.css('a[href]')
    for link in links:
        href = link.attrib.get('href', '')
        if not href:
            continue
        if '/maps/place/' in href:
            continue
        if 'google.com' in href:
            continue
        if href.startswith('http'):
            return href
        # Could be /url?q= pattern
        if '/url?q=' in href:
            match = re.search(r'/url\?q=(https?://[^&]+)', href)
            if match:
                return match.group(1)
    return ""

def looks_like_phone(text: str) -> bool:
    digits = re.sub(r'\D', '', text)
    return 7 <= len(digits) <= 15

def score_lead(has_website: bool, rating: str) -> int:
    score = 2
    if has_website:
        score = 3
    if rating:
        try:
            r = float(rating)
            if r >= 4.0:
                score += 1
        except:
            pass
    return min(score, 5)

def scroll_page(page, scrolls: int = 8):
    for i in range(scrolls):
        try:
            page.evaluate("""
                (() => {
                    const els = Array.from(document.querySelectorAll('div[role="main"] div'));
                    const c = els.find(el => el.scrollHeight > el.clientHeight + 100);
                    if (c) { c.scrollBy(0, 800); return 'ok'; }
                    window.scrollBy(0, 800);
                })()
            """)
        except:
            pass
        time.sleep(random.uniform(1.0, 2.0))

def fetch_and_parse(query: str, city: str) -> List[dict]:
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}+{city.replace(' ', '+')}"
    print(f"\n🔍 {query} in {city}")
    print(f"   URL: {url[:100]}...")
    
    for attempt in range(2):
        try:
            response = StealthyFetcher.fetch(
                url, headless=True, network_idle=True, wait=5000
            )
            # Scroll to load more results
            scroll_page(response, scrolls=8)
            time.sleep(2)
            
            cards = response.css('[role="article"]')
            if not cards:
                place_links = response.css('a[href*="/maps/place/"]')
                seen = set()
                parents = []
                for link in place_links:
                    parent = link.parent
                    if parent is not None:
                        pid = id(parent)
                        if pid not in seen:
                            seen.add(pid)
                            parents.append(parent)
                # Fallback: find parent containers by looking up the tree
                if parents:
                    cards = parents
                else:
                    # Try alt selectors
                    cards = response.css('div[aria-label]')
                    cards = [c for c in cards if len(c.attrib.get('aria-label', '')) > 5]
            
            print(f"   Found {len(cards)} cards")
            
            leads = []
            for card in cards[:MAX_PER_QUERY]:
                try:
                    text = card.get_all_text() or ""
                    if not text or len(text) < 5:
                        continue
                    
                    parsed = parse_card_text(text, query)
                    name = parsed["name"]
                    if not name or len(name) < 2:
                        continue
                    
                    skip = {'google', 'add a missing place', 'add your business', 'your location', 'directions'}
                    if name.lower() in skip:
                        continue
                    
                    website = extract_website_from_card(card)
                    
                    lead = {
                        "company": name,
                        "website": website,
                        "industry": parsed.get("category", query),
                        "city": city,
                        "location": parsed.get("address", city),
                        "phone": parsed.get("phone", ""),
                        "rating": parsed.get("rating", ""),
                        "signal": "Google Maps",
                        "signal_detail": f"Maps: '{query}' in {city}",
                        "score": score_lead(bool(website), parsed.get("rating", "")),
                        "routing": "ryzentic",
                        "notes": f"Found via '{query}' in {city}. Website: {'yes' if website else 'no'}",
                        "scraped_at": datetime.now(timezone.utc).isoformat()
                    }
                    leads.append(lead)
                    print(f"   ✅ {lead['company']} | Score: {lead['score']} | Web: {'yes' if website else 'no'}")
                except Exception as e:
                    continue
            
            return leads
        except Exception as e:
            print(f"   ⚠️ Attempt {attempt+1} failed: {e}")
            time.sleep(3)
    
    return []

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("🚀 Hyderabad Test Lead Scraper (target: 10 leads)")
    print("=" * 55)
    all_leads = []
    seen = set()
    
    for query in QUERIES:
        if len(all_leads) >= TARGET_TOTAL:
            break
        leads = fetch_and_parse(query, CITY)
        for lead in leads:
            if lead["company"] not in seen and len(all_leads) < TARGET_TOTAL:
                seen.add(lead["company"])
                all_leads.append(lead)
        time.sleep(3)
    
    print(f"\n{'='*55}")
    print(f"📊 Total: {len(all_leads)} leads")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_leads, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved to {OUTPUT_FILE}")
