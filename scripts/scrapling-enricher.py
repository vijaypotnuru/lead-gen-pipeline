#!/usr/bin/env python3
"""
Scrapling Enricher v2 — Lightweight by default, browser only when needed.
- Fetcher (HTTP+TLS impersonation): 0 RAM, fast, handles 80% of sites
- StealthyFetcher (headless browser): only for Cloudflare-protected sites
- Domain pre-check: skip dead domains before wasting time
- find_by_text + find_similar for person discovery
"""

import argparse, json, random, re, smtplib, socket, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from scrapling import Fetcher, StealthyFetcher

LEADS_DIR = Path("/mnt/d/leads-folder")
DEFAULT_FILES = ["ryzentic_maps.json", "grovitt_maps.json", "company_tech_signals.json"]
MAX_PER_RUN = 50; MIN_SCORE = 3; SMTP_TO = 5
GENERIC = ["info","contact","hello","sales","support","admin","mail"]
ROLES = ["CEO","Founder","Co-Founder","CTO","CMO","COO","CFO","Director","VP","President","Head of","Manager","Lead"]
NOISE = {'and','the','of','in','for','our','we','is','are','inc','ltd','llc','co','pvt','group','solutions','technologies','services','consulting','agency','studio','software','digital','marketing','media','design','development','security','cloud','company','brand','business','system','international','global','traffic','management','compliance','environmental','regulations','values','ethics','product','quality','process','control','innovation','strategy','creative','content','social','mobile','web','app','network','infrastructure','platform','analytics','growth','performance','experience','transform','excellence','agile','professional','partner','client','customer','engineer','architecture','framework'}

@dataclass
class Person:
    name:str="";role:str="";linkedin:str="";twitter:str="";github:str="";email:str=""
    def to_dict(self):return {k:v for k,v in asdict(self).items() if v}

@dataclass
class Lead:
    company:str="";website:str="";industry:str="";location:str="";size:str=""
    signal:str="";signal_detail:str="";contact_email:str="";linkedin:str=""
    phone:str="";source:str="";score:int=2;routing:str="both"
    notes:str="";search_query:str="";city:str=""
    scraped_at:str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
    people:List[Dict[str,str]]=field(default_factory=list)
    _extra:Dict[str,Any]=field(default_factory=dict,repr=False)
    def to_dict(self)->dict:
        d=asdict(self);d.pop("_extra",None)
        for k,v in self._extra.items():
            if k not in d:d[k]=v
        return d
    @classmethod
    def from_dict(cls,raw:dict)->"Lead":
        known={f.name for f in cls.__dataclass_fields__.values()}
        kw={};ex={}
        for k,v in raw.items():(kw if k in known else ex)[k]=v
        l=cls(**kw);l._extra=ex;return l

def norm_url(s:str)->Optional[str]:
    if not s:return None
    s=s.strip()
    if not s.startswith("http"):s="https://"+s
    try:p=urlparse(s);return f"{p.scheme}://{p.netloc}" if p.netloc else None
    except:return None

def domain_alive(domain:str)->bool:
    """Quick pre-check: is this domain reachable?"""
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        s.settimeout(3)
        r=s.connect_ex((domain,443))
        s.close()
        return r==0
    except:return False

def name_ok(text:str)->bool:
    text=text.strip()
    if not text or len(text)<3 or len(text)>50:return False
    words=text.split()
    if len(words)<2 or len(words)>4:return False
    for w in words:
        if w.lower() in NOISE:return False
        if len(w)>15 or not w[0].isalpha() or not w[0].isupper():return False
    cap=[w for w in words if w[0].isupper() and len(w)>=2]
    return len(cap)>=2

def get_mx(domain:str)->List[str]:
    try:
        import dns.resolver
        a=dns.resolver.resolve(domain,'MX')
        return [str(x.exchange).rstrip('.') for x in sorted(a,key=lambda a:a.preference)]
    except:return[domain]

def smtp_ok(email:str,host:str)->bool:
    try:
        with smtplib.SMTP(host,25,timeout=SMTP_TO) as s:
            s.ehlo_or_helo_if_needed()
            s.mail("v@x.com")
            return s.rcpt(email)[0]==250
    except:return False

def fetch_page(url:str):
    """Try Fetcher first (lightweight HTTP), fall back to StealthyFetcher."""
    try:
        r=Fetcher.get(url,impersonate='chrome',stealthy_headers=True,timeout=12)
        if r.status==200 and len(r.text)>200:return r
    except:pass
    try:
        StealthyFetcher.adaptive=True
        r=StealthyFetcher.fetch(url,headless=True,network_idle=True,wait=3000,timeout=20000)
        return r
    except:return None

def extract_emails(text:str)->List[str]:
    ems,seen=[],set()
    for m in re.finditer(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}',text,re.I):
        e=m.group(0).lower().strip(".")
        if e in seen or len(e)>254:continue
        if any(s in e for s in('example.com','sentry.io','w3.org','@pic.','.png','.jpg')):continue
        seen.add(e);ems.append(e)
    return ems

def find_people(page)->List[Person]:
    people=[]
    try:
        all_text=page.get_all_text() or ""
    except:return people

    # Strategy: find role keywords in text, extract nearby names
    lines=[l.strip() for l in all_text.split('\n') if l.strip()]
    for i,line in enumerate(lines):
        for role in ROLES:
            if role.lower() in line.lower():
                idx=line.lower().index(role.lower())
                before=line[:idx].strip()
                parts=before.split()
                # Try last 2-3 words as name
                for n in[3,2]:
                    if len(parts)>=n:
                        candidate=' '.join(parts[-n:]).strip(' ,.;:-—')
                        if name_ok(candidate):
                            p=Person(name=candidate,role=line[idx:].strip()[:60])
                            if not any(x.name.lower()==p.name.lower() for x in people):
                                people.append(p)
                            break
                break
            if role.lower() in line.lower():break
        if len(people)>=5:break

    # Also check nearby lines (name on line before role)
    if len(people)==0:
        for i,line in enumerate(lines):
            for role in ROLES:
                if role.lower() in line.lower() and i>0:
                    prev=lines[i-1].strip()
                    if name_ok(prev):
                        p=Person(name=prev,role=line.strip()[:60])
                        if not any(x.name.lower()==p.name.lower() for x in people):
                            people.append(p)
                    break
    return people[:5]

def enrich(lead:Lead)->int:
    base=norm_url(lead.website)
    if not base:return 0
    domain=urlparse(base).netloc.lower().lstrip("www.")
    if not domain or not domain_alive(domain):return 0

    new=0
    page=fetch_page(base)
    if not page:return 0

    try:
        all_text=page.get_all_text() or ""
    except:all_text=""

    # --- Email ---
    if not lead.contact_email:
        ems=extract_emails(all_text)
        domain_ems=[e for e in ems if domain in e.split('@')[-1]]
        if domain_ems:
            lead.contact_email=domain_ems[0];new+=1
        else:
            mx=get_mx(domain)
            for pfx in GENERIC:
                e=f"{pfx}@{domain}"
                try:
                    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
                        s.settimeout(2)
                        if s.connect_ex((mx[0],25))==0:
                            if smtp_ok(e,mx[0]):
                                lead.contact_email=e;new+=1;break
                except:pass

    # --- People ---
    if not lead.people:
        ppl=find_people(page)
        if ppl:
            lead.people=[p.to_dict() for p in ppl];new+=len(ppl)

    # --- Person emails ---
    if lead.people:
        mx=get_mx(domain)
        if mx:
            for pdict in lead.people:
                if pdict.get("email"):continue
                n=pdict.get("name","")
                parts=n.lower().split()
                if len(parts)<2:continue
                f,l=parts[0],parts[-1];fi=f[0]if f else""
                patterns=[f"{f}.{l}@{domain}",f"{fi}{l}@{domain}",f"{f}@{domain}",
                          f"{f}_{l}@{domain}",f"{fi}.{l}@{domain}",f"{l}@{domain}"]
                for e in patterns:
                    if smtp_ok(e,mx[0]):pdict["email"]=e;new+=1;break
    return new

def process_file(fp:Path,max_l:int,dry:bool,ms:int)->int:
    with open(fp,"r",encoding="utf-8")as f:raw=json.load(f)
    if not raw:print(f"  {fp.name}: empty");return 0
    leads=[Lead.from_dict(r)for r in raw]
    cand=[l for l in leads if l.website and l.score>=ms]
    # Filter already-enriched
    need=[l for l in cand if not(l.contact_email and l.people)][:max_l]
    print(f"\n📄 {fp.name}: {len(need)}/{len(leads)} need enrichment")

    total=enr=0
    for i,lead in enumerate(need,1):
        nm=lead.company[:40]if lead.company else"?"
        print(f"  [{i}/{len(need)}] {nm} ... ",end="",flush=True)
        n=enrich(lead);total+=n
        if n>0:enr+=1
        parts=[]
        if lead.contact_email:parts.append(f"✉ {lead.contact_email}")
        if lead.people:parts.append(f"{len(lead.people)}👤")
        print(f"{'✅' if parts else '❌'} {' | '.join(parts)}")
        time.sleep(random.uniform(0.5,1.5))

    if not dry and total>0:
        with open(fp,"w",encoding="utf-8")as f:
            json.dump([l.to_dict()for l in leads],f,indent=2,ensure_ascii=False)
        print(f"  💾 Saved ({total} items)")

    e=len([l for l in leads if l.contact_email])
    p=len([l for l in leads if l.people])
    pe=sum(len([x for x in l.people if x.get("email")])for l in leads)
    print(f"  📊 Email:{e}/{len(leads)} People:{p}/{len(leads)} Person-email:{pe}")
    return total

def main():
    p=argparse.ArgumentParser();p.add_argument("--source");p.add_argument("--max",type=int,default=MAX_PER_RUN)
    p.add_argument("--dry-run",action="store_true");p.add_argument("--min-score",type=int,default=MIN_SCORE)
    a=p.parse_args()
    files=[LEADS_DIR/a.source]if a.source else[LEADS_DIR/f for f in DEFAULT_FILES]
    files=[f for f in files if f.exists()]
    if not files:print("No files");return
    s=time.time();t=0
    for fp in files:t+=process_file(fp,a.max,a.dry_run,a.min_score)
    print(f"\n✅ {t} items in {time.time()-s:.0f}s")
if __name__=="__main__":main()
