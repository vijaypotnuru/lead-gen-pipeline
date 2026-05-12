# Lead Generation Pipeline 🎯

End-to-end automated B2B lead generation: scrape Google Maps, GitHub trending, HN hiring signals — enrich with emails, LinkedIn profiles, and decision-maker details. Push structured data to Google Sheets.

**1,939 leads across 12+ global cities | 3 data sources | Free & open-source**

## Pipeline Architecture

```
Google Maps (Scrapling) → 1,803 company leads
GitHub Trending          →    46 tech signals
HN Who is Hiring         →    80 startup signals
                            ─────────
                            1,929 leads
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
              Email Enrich   LinkedIn      Person
              (v4 multi-    Person        Details
               strategy)    Enricher       (WIP)
                    │            │            │
                    └────────────┼────────────┘
                                 ▼
                          Google Sheets
                         (3 tabs, 15 cols)
```

## Companies Served

- **Ryzentic** — Digital agency: software dev, cloud, security, automation
- **Grovitt** — Digital studio: brand, performance marketing, growth

## Scripts

| Script | Purpose | Hit Rate |
|--------|---------|:--------:|
| `maps-lead-scraper.py` | Google Maps scraping (525 lines) | 80-90% |
| `github-lead-scraper.py` | GitHub trending + HN hiring via Algolia API | 100% |
| `email-enricher-v4.py` | Multi-strategy email finder | 60% |
| `linkedin-person-enricher.py` | LinkedIn profile dorking via Google | 30% |
| `person-enricher.py` | Website team page scraping (experimental) | 15% |
| `push-leads-to-sheet.py` | Google Sheets push with formatting | — |
| `startup-lead-scraper.py` | BetaList, HN Show, GitHub startup signals | — |
| `hn-hiring-scraper.py` | HN Who is Hiring standalone | — |

## Enrichment — How It Works (v2.0)

**Unified Scrapling enricher** — single pass per lead discovers both people AND emails:

1. **StealthyFetcher** (headless browser) fetches company website with anti-bot bypass
2. **`find_by_text()`** — locates "Our Team", "Leadership", "About Us" sections by text content
3. **`find_similar()`** — once one person card is found, automatically discovers all similar cards
4. **Email extraction** — regex from rendered page text + SMTP MX handshake verification for generic patterns (info@, contact@, etc.)
5. **Person email guessing** — generates 9+ email patterns per known person name and SMTP-verifies each

**Features:** `auto_save=True` on all selectors (survives site redesigns), `StealthyFetcher.adaptive` (bypasses Cloudflare),
concurrent-ready Spider framework for scale.

**Zero paid APIs. Pure Scrapling + SMTP.**

## Quick Start

```bash
# Install dependencies
pip install scrapling[all] dnspython requests google-auth google-api-python-client

# Scrape Google Maps leads
python3 scripts/maps-lead-scraper.py --company both

# Scrape tech signals
python3 scripts/github-lead-scraper.py

# Enrich with emails
python3 scripts/email-enricher-v4.py --source ryzentic_maps.json --max 500

# Push to Google Sheets
python3 scripts/push-leads-to-sheet.py
```

## Setup

- **Scrapling**: `pip install "scrapling[all]>=0.4.8" && scrapling install --force`
- **Google Sheets auth**: Gog CLI (`GOG_KEYRING_PASSWORD` + `GOG_ACCOUNT` in env)
- **Lead data**: Stored in `D:\leads-folder` (`/mnt/d/leads-folder/` in WSL)

## Data Files

| Path | Content |
|------|---------|
| `D:\leads-folder\ryzentic_maps.json` | Ryzentic leads (1,021) |
| `D:\leads-folder\grovitt_maps.json` | Grovitt leads (792) |
| `D:\leads-folder\company_tech_signals.json` | Tech signals (126) |
| `D:\leads-folder\hyderabad_test_leads.json` | Hyderabad test leads (10) |

## Sheet Structure

**15 Columns:** #, Company, Website, Industry, City, Location, Phone, Email, Rating, Signal, Signal Detail, Score, Routing, Key People, Notes

[Google Sheet](https://docs.google.com/spreadsheets/d/1LbFbaadqk9QgMkPA9XjcHDSCS1KIahepzA7DQ55sZ_k/edit)

## License

MIT
