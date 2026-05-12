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

## Email Enricher v4 — How It Works

Multi-strategy cascade (tries in order):

1. **Website scraping** — crawls `/contact`, `/about`, `/about-us`, `/` for email addresses
2. **SMTP pattern guessing** — tries common prefixes (info@, contact@, sales@, etc.) and verifies via SMTP MX handshake
3. **Person email guessing** — when people data exists, generates 9+ email patterns per person and SMTP-verifies each

**No paid APIs. Zero cost.**

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
