#!/usr/bin/env python3
"""
Push leads from D:/leads-folder to Google Sheets with proper formatting.
Requires: gog (for OAuth token export), google-auth, google-api-python-client
"""

import json, os, subprocess, tempfile
from pathlib import Path

SID = "1LbFbaadqk9QgMkPA9XjcHDSCS1KIahepzA7DQ55sZ_k"
LEADS_DIR = Path("/mnt/d/leads-folder")
GOG_ENV = {"GOG_KEYRING_PASSWORD": "openclaw", "GOG_ACCOUNT": "vijaypotnuru123@gmail.com", **os.environ}

HEADERS_MAPS = ["#","Company","Website","Industry","City","Location","Phone","Email","Rating","Signal","Signal Detail","Score","Routing","Key People","Notes"]
HEADERS_TECH = ["#","Company","Website","Industry","Location","Signal","Source","Score","Routing","Key People","Notes"]


def get_service():
    """Get an authenticated Sheets service object."""
    tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tf.close()
    try:
        r = subprocess.run(
            ["gog", "auth", "tokens", "export", "vijaypotnuru123@gmail.com", "--out", tf.name, "--overwrite"],
            capture_output=True, text=True, env=GOG_ENV, timeout=30
        )
        if r.returncode != 0:
            print(f"Token export failed: {r.stderr[:200]}")
            return None

        with open(tf.name) as f:
            token_data = json.load(f)

        with open("/home/vijay/.config/gogcli/credentials.json") as f:
            creds_data = json.load(f)

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials(
            token=None, refresh_token=token_data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data["client_id"], client_secret=creds_data["client_secret"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        creds.refresh(Request())

        from googleapiclient.discovery import build
        return build("sheets", "v4", credentials=creds)
    except Exception as e:
        print(f"Auth error: {e}")
        return None
    finally:
        if os.path.exists(tf.name):
            os.unlink(tf.name)


def format_people(lead: dict) -> str:
    """Format people list into a readable string for the sheet cell."""
    people = lead.get("people", [])
    if not people:
        return ""
    parts = []
    for p in people:
        name = p.get("name", "")
        role = p.get("role", "")
        li = p.get("linkedin", "")
        line = name
        if role:
            line += f" ({role})"
        if li:
            line += f" [{li}]"
        parts.append(line)
    return " | ".join(parts)


def build_map_rows(leads):
    rows = [HEADERS_MAPS]
    for i, l in enumerate(leads, 1):
        sd = l.get("signal_detail", "")
        rating = sd.split("Rating:")[-1].split("|")[0].strip() if "Rating:" in sd else "-"
        rows.append([
            str(i), l.get("company",""), l.get("website",""), l.get("industry",""),
            l.get("city",""), l.get("location",""), l.get("phone",""), (l.get("contact_email","") or l.get("Email","")), rating,
            l.get("signal",""), sd, str(l.get("score","")), l.get("routing",""), format_people(l), l.get("notes",""),
        ])
    return rows


def build_tech_rows(leads):
    rows = [HEADERS_TECH]
    for i, l in enumerate(leads, 1):
        rows.append([
            str(i), l.get("company",""), l.get("website",""), l.get("industry",""),
            l.get("location","") or l.get("city",""), l.get("signal",""),
            l.get("source",""), str(l.get("score","")), l.get("routing",""), format_people(l), l.get("notes",""),
        ])
    return rows


def push_tab(service, tab_name, rows, num_cols):
    """Push rows to a tab, formatting the header."""
    service.spreadsheets().values().clear(spreadsheetId=SID, range=f"{tab_name}!A:{chr(64+num_cols)}").execute()
    service.spreadsheets().values().update(
        spreadsheetId=SID, range=f"{tab_name}!A1", valueInputOption="RAW", body={"values": rows}
    ).execute()
    print(f"  Wrote {len(rows)} rows to {tab_name}")

    meta = service.spreadsheets().get(spreadsheetId=SID).execute()
    sid = next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == tab_name)
    requests = [
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 11}, "backgroundColor": {"red": 0.16, "green": 0.4, "blue": 0.33}, "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)"
        }},
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": num_cols}}}
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": requests}).execute()
    print(f"  Formatted {tab_name} ✓")


def ensure_tab(service, tab_name):
    meta = service.spreadsheets().get(spreadsheetId=SID).execute()
    if not any(s["properties"]["title"] == tab_name for s in meta["sheets"]):
        service.spreadsheets().batchUpdate(spreadsheetId=SID, body={
            "requests": [{"addSheet": {"properties": {"title": tab_name}}}]
        }).execute()
        print(f"  Created '{tab_name}' tab")


def main():
    service = get_service()
    if not service:
        print("Failed to authenticate")
        return

    # Load & push Ryzentic maps
    with open(LEADS_DIR / "ryzentic_maps.json") as f:
        ryz = json.load(f)
    push_tab(service, "Ryzentic", build_map_rows(ryz), 15)

    # Load & push Grovitt maps
    with open(LEADS_DIR / "grovitt_maps.json") as f:
        gro = json.load(f)
    push_tab(service, "Grovitt", build_map_rows(gro), 15)

    # Load & push tech signals
    tech_leads = []
    for fname in ["company_tech_signals.json", "hn_hiring_leads.json", "startup_leads.json"]:
        path = LEADS_DIR / fname
        if path.exists():
            with open(path) as f:
                tech_leads.extend(json.load(f))

    if tech_leads:
        ensure_tab(service, "Tech Signals")
        push_tab(service, "Tech Signals", build_tech_rows(tech_leads), 11)

    total = len(ryz) + len(gro) + len(tech_leads)
    print(f"\n✅ https://docs.google.com/spreadsheets/d/{SID}/edit")
    print(f"   Ryzentic: {len(ryz)} | Grovitt: {len(gro)} | Tech Signals: {len(tech_leads)}")
    print(f"   TOTAL: {total} leads")


if __name__ == "__main__":
    main()
