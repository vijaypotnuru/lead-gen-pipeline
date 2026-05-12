---
name: lead-generation-pipeline
description: End-to-end lead generation pipeline using Scrapling for Google Maps scraping, GitHub trending, HN hiring signals, and Google Sheets for structured output. Automates lead discovery, scoring, routing, and sheet population for sales outreach. Use when asked to generate leads, find businesses, do market research, or populate prospect sheets.
version: "2.0.0"
license: MIT
metadata:
  openclaw:
    emoji: "🎯"
    requires:
      bins: [python3, gog]
      env:
        - GOG_KEYRING_PASSWORD
        - GOG_ACCOUNT
      skills:
        - scrapling-official
---

# Lead Generation Pipeline

End-to-end automated lead generation: scrape Google Maps for businesses that need digital services, GitHub trending for active companies, HN Who is Hiring for hiring signals — score, route, and push structured data to Google Sheets.

**Current state (2026-05-11):** 1,929 leads across 12+ global cities, 3 data sources, 3 sheet tabs.

## Companies Served

- **Ryzentic** — Digital agency: software dev, cloud, security, automation, websites, analytics. Routing: `ryzentic`
- **Grovitt** — Digital studio: brand, performance marketing, campaigns, growth. Routing: `grovitt`

## Architecture

```
Phase 1: Scrapling (StealthyFetcher) → Google Maps    → 1,803 leads
Phase 2: Scrapling + Algolia API    → GitHub/HN       →   126 leads
Phase 3: (not built yet)            → LinkedIn/Indeed  →     0 leads
                                     ─────────────────
                                     Total:             1,929 leads
                                              ↓
                                     Scoring + Routing engine
                                              ↓
                              Google Sheets (3 tabs, formatted)
```

## Setup

```bash
pip install "scrapling[all]>=0.4.8" google-auth google-api-python-client --break-system-packages
scrapling install --force
```

Google Sheets auth via Gog CLI:
```bash
GOG_KEYRING_PASSWORD=*** GOG_ACCOUNT=vijaypotnuru123@gmail.com
```

All lead data lives in `D:\leads-folder` (`/mnt/d/leads-folder/` in WSL).

## Script Inventory

| Script | Purpose | Status |
|--------|---------|--------|
| `scripts/maps-lead-scraper.py` | Google Maps scraping engine (525 lines) | ✅ Working |
| `scripts/push-leads-to-sheet.py` | Sheets auth, push, format (158 lines) | ✅ Working |
| `scripts/github-lead-scraper.py` | GitHub trending + HN hiring via Algolia API | ✅ Working |
| `scripts/hn-hiring-scraper.py` | HN Who is Hiring standalone (Algolia API) | ✅ Working |

## Phase 1: Google Maps

### How it works
1. Defines search queries × cities per company
2. `StealthyFetcher.fetch(url, headless=True, network_idle=True, wait=5000)`
3. Parses result cards via text extraction (name, address, phone, website, rating, category)
4. Deduplicates across queries, scores, saves to JSON

### Running

```bash
cd /home/vijay/.openclaw/workspace
python3 scripts/maps-lead-scraper.py --company ryzentic    # 11 queries × 5 cities
python3 scripts/maps-lead-scraper.py --company grovitt     # 10 queries × 5 cities
python3 scripts/maps-lead-scraper.py --company both        # both
```

### Ryzentic Query Matrix
| Query | Cities |
|-------|--------|
| software development company | New York, London, Singapore, Dubai, Sydney |
| IT services company | Los Angeles, Toronto, Berlin, Mumbai, Sydney |
| cloud consulting firm | San Francisco, London, Dubai, Singapore, Sydney |
| cybersecurity company | New York, London, Tel Aviv, Singapore, Dubai |
| digital transformation agency | London, New York, Dubai, Singapore, Sydney |
| automation solutions | San Francisco, London, Berlin, Toronto, Dubai |
| restaurant | New York, London, Paris, Dubai, Singapore |
| hotel | Dubai, London, Paris, New York, Singapore |
| dental clinic | London, New York, Toronto, Sydney, Dubai |
| law firm | New York, London, Dubai, Singapore, Toronto |
| real estate agency | Dubai, London, New York, Miami, Sydney |

### Grovitt Query Matrix
| Query | Cities |
|-------|--------|
| marketing agency | London, New York, Los Angeles, Dubai, Singapore |
| branding agency | London, New York, Paris, Dubai, Sydney |
| ecommerce business | London, New York, Dubai, Singapore, Berlin |
| DTC brand | London, New York, Los Angeles, Dubai, Toronto |
| growth marketing agency | London, San Francisco, New York, Dubai, Singapore |
| performance marketing | London, New York, Dubai, Singapore, Sydney |
| restaurant | London, New York, Dubai, Paris, Singapore |
| fashion boutique | London, Paris, New York, Dubai, Milan |
| startup incubator | London, San Francisco, New York, Berlin, Dubai |
| SaaS company | San Francisco, London, New York, Dubai, Singapore |

## Phase 2: Tech Signals (GitHub + HN)

### GitHub Trending
```bash
python3 scripts/github-lead-scraper.py
```
Scrapes trending repos across 5 languages (all, TypeScript, Python, Go, Rust). Uses verified CSS selector: `article.Box-row`, extracts via `h2 a` for repo name, `p` for description.

### HN Who is Hiring
```bash
python3 scripts/hn-hiring-scraper.py
```
Uses free Algolia API to find latest thread, then pulls comments. Extracts company name from `**bold**`, website from first URL, location from "remote/hybrid/onsite" patterns. Thread ID: `47975571` (May 2026).

## Phase 3: Sheets Output

### Pushing data

```bash
python3 scripts/push-leads-to-sheet.py
```

The script:
1. Exports Gog OAuth token, refreshes via google-auth
2. Reads all JSON files from `D:\leads-folder`
3. Pushes to 3 tabs with proper 2D value arrays

### Sheet Structure

**SID:** `1LbFbaadqk9QgMkPA9XjcHDSCS1KIahepzA7DQ55sZ_k`
**URL:** https://docs.google.com/spreadsheets/d/1LbFbaadqk9QgMkPA9XjcHDSCS1KIahepzA7DQ55sZ_k/edit

**Ryzentic tab** (13 columns): #, Company, Website, Industry, City, Location, Phone, Rating, Signal, Signal Detail, Score, Routing, Notes

**Grovitt tab** (13 columns): Same schema

**Tech Signals tab** (10 columns): #, Company, Website, Industry, Location, Signal, Source, Score, Routing, Notes

All tabs: frozen header row, bold white text on #296654 background, auto-resized columns.

### Scoring
| Score | Condition |
|-------|-----------|
| 5 | Recently funded + actively hiring + bad/no website |
| 4 | Has complete contact info + high rating, OR trending/hiring signal |
| 3 | Solid business presence, clear category fit |
| 2 | Possible need but unclear |
| 1 | Low signal |

### Routing
| Route | Keywords |
|-------|----------|
| `ryzentic` | software, dev, cloud, security, automation, IT, infrastructure, API, backend, database |
| `grovitt` | marketing, brand, growth, advertising, content, social media, e-commerce, DTC |
| `both` | SaaS platform, startup, funded, hiring cross-functional |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Missing X server" | Always use `headless=True` in WSL |
| "Page.goto timeout" | Add `network_idle=True, wait=5000` for Maps |
| Maps returns 0 leads | Google A/B tests UI; card text parser may need adjustment |
| Product Hunt returns 403 | Cloudflare-level block; can't bypass without paid proxy |
| YC page shows "no companies" | React hydration; data loads via XHR, needs network interception |
| Token export fails | Ensure `GOG_KEYRING_PASSWORD` is set in non-TTY env |
| Sheets range mismatch | Use the push script (auto-calculates range from row count) |

## Data Files

| Path | Content |
|------|---------|
| `D:\leads-folder\ryzentic_maps.json` | Ryzentic leads (1,011) |
| `D:\leads-folder\grovitt_maps.json` | Grovitt leads (792) |
| `D:\leads-folder\company_tech_signals.json` | GitHub trending leads (46) |
| `D:\leads-folder\hn_hiring_leads.json` | HN hiring leads |
| `D:\leads-folder\startup_leads.json` | Startup source leads (80) |
| `D:\leads-folder\ryzentic_leads.csv` | CSV backup |
| `D:\leads-folder\grovitt_leads.csv` | CSV backup |
