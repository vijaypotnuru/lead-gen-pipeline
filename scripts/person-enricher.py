#!/usr/bin/env python3
"""
Person Enricher v2 — Scrape company websites for decision-maker info.
Strategy: JSON-LD schema data → HTML structure → text pattern matching.
Extracts names, roles, LinkedIn, Twitter/X, GitHub.

Usage:
    python3 scripts/person-enricher.py
    python3 scripts/person-enricher.py --source hyderabad_test_leads.json --min-score 2
"""

import argparse, json, random, re, sys, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LEADS_DIR = Path("/mnt/d/leads-folder")
LEADS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]
TEAM_PAGES = ["/about", "/about-us", "/team", "/contact", "/"]
MIN_DELAY, MAX_DELAY = 0.5, 2.0
TIMEOUT = 12
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
MAX_PEOPLE = 5
MAX_LEADS_PER_RUN = 50
MIN_SCORE = 3

ROLE_KEYWORDS_RE = (
    r'(?:CEO|Chief\s+Executive|Founder|Co[\-\s]?Founder|CTO|Chief\s+Technolog|'
    r'CMO|Chief\s+Marketing|COO|Chief\s+Operating|CFO|Chief\s+Financial|CIO|'
    r'Director|VP\b|Vice\s+President|Head\s+of|Manager|Lead|Principal|'
    r'President|Chairman|Managing\s+Director|General\s+Manager|'
    r'Product\s+Manager|Engineering\s+Manager|Technical\s+Lead|Team\s+Lead|'
    r'CXO|Partner|Owner|Proprietor)'
)

# Words that invalidate a potential name
NAME_BLACKLIST = {
    "contact", "email", "phone", "address", "submit", "send", "message",
    "privacy", "policy", "terms", "conditions", "cookie", "login", "sign",
    "register", "home", "services", "products", "careers", "jobs",
    "blog", "news", "press", "media", "partners", "clients", "testimonials",
    "support", "help", "faq", "sitemap", "search", "cart", "menu",
    "linkedin", "twitter", "facebook", "instagram", "youtube",
    "reviews", "rating", "awards", "global", "worldwide", "leading",
    "trusted", "headquarters", "office", "solutions", "get started",
    "learn more", "read more", "view more", "click here", "subscribe",
    "download", "follow us", "all rights", "copyright", "powered by",
    "quick links", "our services", "our clients", "case studies",
}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Person:
    name: str = ""
    role: str = ""
    linkedin: str = ""
    twitter: str = ""
    github: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

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
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_extra", None)
        for k, v in self._extra.items():
            if k not in d:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "Lead":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {}
        extra = {}
        for k, v in raw.items():
            if k in known:
                kwargs[k] = v
            else:
                extra[k] = v
        lead = cls(**kwargs)
        lead._extra = extra
        return lead


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize_url(raw: str) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    try:
        p = urlparse(raw)
        return f"{p.scheme}://{p.netloc}" if p.netloc else None
    except:
        return None


# ---------------------------------------------------------------------------
# Strategy 0: JSON-LD structured data (schema.org Person, founder, etc.)
# ---------------------------------------------------------------------------

def extract_from_jsonld(html: str) -> List[Person]:
    """Pull people from <script type='application/ld+json'> blocks."""
    people: List[Person] = []
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.I
    )
    for block in blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                t = item.get("@type", "")
                if t == "Person":
                    job = item.get("jobTitle", "")
                    same_as = item.get("sameAs", [])
                    if isinstance(same_as, str):
                        same_as = [same_as]
                    p = Person(
                        name=item.get("name", ""),
                        role=job,
                    )
                    for url in same_as:
                        li = classify_social_url(url)
                        if li:
                            setattr(p, li[0], li[1])
                    people.append(p)
                elif t in ("Organization", "LocalBusiness", "Corporation"):
                    # founder, founders, employee
                    for field in ("founder", "founders", "employee", "employees"):
                        vals = item.get(field, [])
                        if isinstance(vals, dict):
                            vals = [vals]
                        for v in vals:
                            if isinstance(v, dict) and v.get("name"):
                                p = Person(name=v.get("name", ""))
                                if p.name:
                                    people.append(p)
                    # author (often the owner for blogs/agencies)
                    author = item.get("author", [])
                    if isinstance(author, dict):
                        author = [author]
                    for a in author:
                        if isinstance(a, dict) and a.get("name"):
                            p = Person(name=a.get("name", ""))
                            if p.name:
                                people.append(p)
        except:
            pass
    return people


# ---------------------------------------------------------------------------
# Strategy 1: HTML structure — team member cards
# ---------------------------------------------------------------------------

def classify_social_url(href: str) -> Optional[tuple]:
    """Return (attr_name, normalized_url) or None."""
    href = href.strip()
    if "linkedin.com/in/" in href:
        m = re.search(r'linkedin\.com/in/[^/?&\s\"\'<>#]+', href)
        if m:
            return ("linkedin", "https://www." + m.group(0))
    if re.search(r'(?:twitter\.com|x\.com)/[A-Za-z0-9_]{1,15}$', href):
        m = re.search(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})', href)
        if m:
            return ("twitter", "https://x.com/" + m.group(1))
    if re.search(r'github\.com/[A-Za-z0-9_.-]+$', href):
        m = re.search(r'github\.com/([A-Za-z0-9_.-]+)', href)
        if m:
            handle = m.group(1)
            if handle not in ("topics", "search", "explore", "marketplace", "trending", "orgs"):
                return ("github", "https://github.com/" + handle)
    return None


def extract_social_from_html(html: str, base_url: str) -> Dict[str, List[str]]:
    """Extract LinkedIn, Twitter, GitHub URLs from hrefs."""
    results = {"linkedin": [], "twitter": [], "github": []}
    seen = set()
    for href in re.findall(r'href=[\"\']([^\"\']+)[\"\']', html, re.I):
        href = href.strip()
        if href.startswith("/") or (not href.startswith("http") and href):
            href = urljoin(base_url, href)
        result = classify_social_url(href)
        if result:
            key, url = result
            if url not in seen:
                seen.add(url)
                results[key].append(url)
    return results


def looks_like_person_name(text: str) -> bool:
    """Reasonable heuristic for a human name."""
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 45:
        return False
    # Strip common prefixes
    clean = re.sub(r'^(Meet\s+the\s+)?(Team\s+|Our\s+|Mr\.?\s+|Ms\.?\s+|Dr\.?\s+)?', '', text, flags=re.I).strip()
    words = clean.split()
    if len(words) < 2 or len(words) > 4:
        return False

    # Common non-name words that appear in company copy
    noise_words = {
        'and', 'the', 'of', 'in', 'for', 'our', 'we', 'is', 'are', 'to', 'at',
        'with', 'your', 'from', 'has', 'its', 'all', 'can', 'will', 'be', 'an',
        'this', 'that', 'more', 'best', 'new', 'top', 'get', 'one', 'by', 'no',
        'or', 'on', 'as', 'it', 'us', 'com', 'org', 'net', 'inc', 'ltd', 'llc',
        'co', 'pvt', 'private', 'limited', 'first', 'source', 'way', 'solutions',
        'technologies', 'system', 'systems', 'group', 'international', 'global',
        'services', 'consulting', 'agency', 'studio', 'firm', 'business',
        'company', 'brand', 'brands', 'marketing', 'digital', 'media', 'design',
        'development', 'software', 'tech', 'it', 'data', 'cloud', 'security',
        'solution', 'innovation', 'create', 'build', 'manage', 'consult',
        'strategy', 'creative', 'content', 'social', 'mobile', 'web', 'app',
        'traffic', 'management', 'compliance', 'environmental', 'regulations',
        'values', 'ethics', 'product', 'quality', 'process', 'control',
        'service', 'support', 'network', 'infrastructure', 'platform', 'ai',
        'automation', 'analytics', 'growth', 'performance', 'campaign',
        'seo', 'ppc', 'ads', 'advertising', 'research', 'insights',
        'experience', 'transform', 'transformations', 'excellence', 'agile',
        'professional', 'partners', 'partner', 'client', 'customer',
        'engineer', 'architecture', 'architect', 'framework', 'tool',
        'portal', 'dashboard', 'interface', 'integration', 'deployment',
        'startup', 'enterprise', 'scale', 'accelerate', 'optimize',
        'custom', 'tailored', 'bespoke', 'dedicated', 'exclusive',
        'premium', 'elite', 'advanced', 'modern', 'cutting', 'edge',
        'next', 'gen', 'generation', 'industry', 'standard', 'leading',
        'trusted', 'award', 'winning', 'rated', 'certified', 'approved',
        'official', 'authorized', 'licensed', 'registered', 'insured',
    }

    # Check each word
    for w in words:
        # Each word must start with a capital letter
        if not w[0].isalpha() or not w[0].isupper():
            return False
        # Allow apostrophes and hyphens: O'Brien, Anne-Marie
        if re.search(r'[^A-Za-z\'\-\.]', w):
            return False
        # Reject noise words (case-insensitive)
        if w.lower() in noise_words:
            return False
        # Name words are typically 2-12 characters
        if len(w) > 12:
            return False

    # Must have at least two valid name words
    cap_words = [w for w in words if w[0].isupper() and w.lower() not in noise_words]
    if len(cap_words) < 2:
        return False

    # At least one name must be 3+ characters
    if not any(len(w) >= 3 for w in cap_words):
        return False

    # Names should not contain noise_words as any part
    lower = clean.lower()
    if any(bl in lower for bl in NAME_BLACKLIST):
        return False

    return True


def extract_name_role_patterns(text: str) -> List[Person]:
    """Find 'Name — Role' or 'Name, Role' patterns in text."""
    people: List[Person] = []
    # Split text into logical segments at paragraph breaks
    segments = text.split('\n')

    for seg in segments:
        seg = seg.strip()
        if not seg or len(seg) > 200:
            continue

        # Pattern: "Name — Role" with various separators
        # Separator can be: — (em-dash), – (en-dash), - (hyphen), |, ·, :, comma
        m = re.match(
            r'(.{3,45}?)\s*[–—\|·:]+\s*(' + ROLE_KEYWORDS_RE + r'.{0,40}$)',
            seg, re.I
        )
        if not m:
            # Try comma separator (but only if role keyword follows)
            m = re.match(
                r'(.{3,45}?),\s+(' + ROLE_KEYWORDS_RE + r'.{0,40}$)',
                seg, re.I
            )

        if m:
            name = m.group(1).strip()
            role = m.group(2).strip()
            # Strip common prefixes
            name = re.sub(r'^(Meet\s+the\s+)?(Team\s+|Our\s+|Mr\.?\s+|Ms\.?\s+|Dr\.?\s+)?', '', name, flags=re.I).strip()
            if looks_like_person_name(name):
                if not any(p.name.lower() == name.lower() for p in people):
                    people.append(Person(name=name, role=role))

    return people


# ---------------------------------------------------------------------------
# Strategy 2: Full-text scan — name near role keyword
# ---------------------------------------------------------------------------

def extract_name_near_role(text_lines: List[str]) -> List[Person]:
    """When a name appears on one line and a role keyword appears on a nearby line."""
    people: List[Person] = []
    for i, line in enumerate(text_lines):
        line = line.strip()
        # Skip lines that are clearly not person-related
        if not line or len(line) > 200:
            continue

        # Does this line contain a role keyword?
        role_match = re.search(ROLE_KEYWORDS_RE, line, re.I)
        if not role_match:
            continue

        # Get the surrounding text to find the name
        role_text = line

        # Check if name is on the same line (before the role)
        before_role = line[:role_match.start()].strip()
        # Try to extract name from before the role keyword
        name_parts = before_role.split()
        if len(name_parts) >= 2:
            # Take last few words as candidate name
            for n_words in [4, 3, 2]:
                if len(name_parts) >= n_words:
                    candidate = " ".join(name_parts[-n_words:])
                    name = re.sub(r'^(Meet\s+the\s+)?(Team\s+|Our\s+|Mr\.?\s+|Ms\.?\s+|Dr\.?\s+)?', '', candidate, flags=re.I).strip()
                    if looks_like_person_name(name):
                        role = role_match.group(0)
                        # Expand role to include text after the keyword too
                        role = line[role_match.start():].strip(" ,.;:")
                        if not any(p.name.lower() == name.lower() for p in people):
                            people.append(Person(name=name, role=role[:60]))
                        break

        # If name not found inline, check the previous 1-2 lines
        if not any(p.name.lower() == before_role.lower() for p in people):
            for j in range(max(0, i - 2), i):
                prev = text_lines[j].strip()
                name = re.sub(r'^(Meet\s+the\s+)?(Team\s+|Our\s+|Mr\.?\s+|Ms\.?\s+|Dr\.?\s+)?', '', prev, flags=re.I).strip()
                if looks_like_person_name(name) and len(name.split()) >= 2:
                    role = role_match.group(0)
                    role = line[role_match.start():].strip(" ,.;:")[:60]
                    if not any(p.name.lower() == name.lower() for p in people):
                        people.append(Person(name=name, role=role))
                    break

    return people


# ---------------------------------------------------------------------------
# Fetch + extract
# ---------------------------------------------------------------------------

def clean_html_to_text(html: str) -> str:
    """Extract readable lines from HTML."""
    import html as _hlib
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<noscript[^>]*>.*?</noscript>", "", text, flags=re.DOTALL | re.I)
    # Block-level tags → newlines
    text = re.sub(r"<\s*/\s*(div|p|li|h[1-6]|section|article|tr|br|hr)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*(br|hr)\s*/?\s*>", "\n", text, flags=re.I)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = _hlib.unescape(text)
    # Clean whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    return text.strip()


def fetch_page(session: requests.Session, url: str) -> Optional[str]:
    try:
        r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA}, allow_redirects=True)
        if r.status_code == 200 and len(r.text) > 300:
            return r.text
    except:
        pass
    return None


def scrape_company_website(base_url: str) -> List[Person]:
    """Fetch pages, extract people using all strategies."""
    session = requests.Session()
    session.max_redirects = 5
    accumulated_html = ""
    accumulated_text = ""

    for page in TEAM_PAGES:
        url = base_url.rstrip("/") + page if page != "/" else base_url
        html = fetch_page(session, url)
        if html:
            accumulated_html += "\n" + html
            accumulated_text += "\n" + clean_html_to_text(html)

    if not accumulated_html:
        return []

    all_people: List[Person] = []

    # Strategy 0: JSON-LD (most reliable when available)
    jsonld_people = extract_from_jsonld(accumulated_html)
    for p in jsonld_people:
        if p.name and not any(x.name.lower() == p.name.lower() for x in all_people):
            all_people.append(p)

    # Strategy 1: Name — Role patterns in text
    pattern_people = extract_name_role_patterns(accumulated_text)
    for p in pattern_people:
        if p.name and not any(x.name.lower() == p.name.lower() for x in all_people):
            all_people.append(p)

    # Strategy 2: Name near role keyword in text lines
    if len(all_people) < MAX_PEOPLE:
        lines = [l.strip() for l in accumulated_text.split("\n") if l.strip()]
        nearby_people = extract_name_near_role(lines)
        for p in nearby_people:
            if p.name and not any(x.name.lower() == p.name.lower() for x in all_people):
                all_people.append(p)

    # Cap at MAX_PEOPLE
    all_people = all_people[:MAX_PEOPLE]

    # Enrich with social links
    if all_people:
        social = extract_social_from_html(accumulated_html, base_url)
        for i, person in enumerate(all_people):
            if not person.linkedin and i < len(social["linkedin"]):
                person.linkedin = social["linkedin"][i]
            if not person.twitter and i < len(social["twitter"]):
                person.twitter = social["twitter"][i]
            if not person.github and i < len(social["github"]):
                person.github = social["github"][i]

    return all_people


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def enrich_file(filepath: Path, max_leads: int, dry_run: bool, min_score: int) -> int:
    with open(filepath, "r", encoding="utf-8") as f:
        raw_leads = json.load(f)

    if not raw_leads:
        print(f"  {filepath.name}: empty, skipping")
        return 0

    leads = [Lead.from_dict(r) for r in raw_leads]
    candidates = [l for l in leads if l.website and not l.people and l.score >= min_score]
    candidates = candidates[:max_leads]

    print(f"\n📄 {filepath.name}: {len(candidates)}/{len(leads)} leads to enrich")

    enriched = 0
    for i, lead in enumerate(candidates, 1):
        base = normalize_url(lead.website)
        if not base:
            continue

        company_name = lead.company[:50] if lead.company else "Unknown"
        print(f"  [{i}/{len(candidates)}] {company_name} ... ", end="", flush=True)

        people = scrape_company_website(base)
        if people:
            lead.people = [p.to_dict() for p in people]
            names = ", ".join(p.name for p in people)
            lead.notes = (lead.notes or "") + f" | People: {names}"
            lead.notes = lead.notes.strip(" |")
            enriched += 1
            print(f"✔ ({len(people)}: {names[:70]})")
        else:
            print("✗")

        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    if not dry_run and enriched > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
        print(f"  💾 Saved {enriched} enriched leads")
    elif dry_run:
        print(f"  🚫 dry-run — would save {enriched}")

    total = len([l for l in leads if l.people])
    print(f"  📊 People data: {total}/{len(leads)}")
    return enriched


def main():
    p = argparse.ArgumentParser(description="Person Enricher v2")
    p.add_argument("--source", default=None, help="Specific JSON file name")
    p.add_argument("--max", type=int, default=MAX_LEADS_PER_RUN, help="Max leads per file")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-score", type=int, default=MIN_SCORE, help="Minimum lead score")
    args = p.parse_args()

    files = [LEADS_DIR / args.source] if args.source else [LEADS_DIR / f for f in DEFAULT_FILES]
    files = [f for f in files if f.exists()]
    if not files:
        print("No files found."); return

    start = time.time()
    total = 0
    for fp in files:
        total += enrich_file(fp, args.max, args.dry_run, args.min_score)
    print(f"\n✅ {total} leads enriched in {time.time()-start:.0f}s")


if __name__ == "__main__":
    main()
