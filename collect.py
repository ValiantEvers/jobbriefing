#!/usr/bin/env python3
"""
Job Briefing — Collector & Scorer v2
Extracts jobs from Finn.no via __NEXT_DATA__ JSON + JSON-LD + HTML fallback.
"""

import json, re, hashlib, urllib.request, time
from datetime import datetime, timezone
from html import unescape

def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5",
}

def fetch_page(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")

def fetch_finn(url, source_name):
    jobs = []
    try:
        html = fetch_page(url)

        # Strategy 1: __NEXT_DATA__
        nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if nd:
            try:
                data = json.loads(nd.group(1))
                jobs = walk_for_jobs(data, source_name)
                if jobs:
                    print(f"    __NEXT_DATA__: {len(jobs)} jobs")
                    return jobs
            except json.JSONDecodeError:
                pass

        # Strategy 2: JSON-LD
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                for item in (ld if isinstance(ld, list) else [ld]):
                    if item.get("@type") == "JobPosting":
                        org = item.get("hiringOrganization", {})
                        jobs.append(make_job(
                            item.get("title", ""), item.get("url", ""),
                            org.get("name", "") if isinstance(org, dict) else "",
                            clean(item.get("description", "")), source_name,
                            item.get("datePosted", ""), extract_loc(item.get("jobLocation"))))
            except (json.JSONDecodeError, TypeError):
                continue
        if jobs:
            print(f"    JSON-LD: {len(jobs)} jobs")
            return jobs

        # Strategy 3: finnkode links in HTML
        for m in re.finditer(r'href="(https://www\.finn\.no/job/[^"]*finnkode=(\d+)[^"]*)"[^>]*>([^<]{5,150})', html):
            link, _, title = m.group(1), m.group(2), unescape(m.group(3)).strip()
            title = re.sub(r"\s+", " ", title)
            if not any(j["link"] == link for j in jobs):
                jobs.append(make_job(title, link, "", "", source_name))
        if jobs:
            print(f"    HTML links: {len(jobs)} jobs")

    except Exception as e:
        print(f"    [WARN] {source_name}: {e}")
    return jobs


def walk_for_jobs(obj, source, depth=0):
    """Recursively find job-like objects in nested JSON."""
    jobs = []
    if depth > 10 or not obj:
        return jobs
    if isinstance(obj, dict):
        if "heading" in obj and ("canonical_url" in obj or "ad_id" in obj):
            ts = obj.get("published") or obj.get("timestamp")
            ds = ""
            if isinstance(ts, (int, float)) and ts > 1e9:
                ds = datetime.fromtimestamp(ts/1000 if ts > 1e12 else ts, tz=timezone.utc).strftime("%Y-%m-%d")
            jobs.append(make_job(
                obj.get("heading", ""), obj.get("canonical_url", ""),
                obj.get("company_name", ""), "", source, ds, obj.get("location", "")))
            return jobs
        if obj.get("@type") == "JobPosting":
            org = obj.get("hiringOrganization", {})
            jobs.append(make_job(
                obj.get("title", ""), obj.get("url", ""),
                org.get("name", "") if isinstance(org, dict) else "",
                clean(obj.get("description", "")), source,
                obj.get("datePosted", ""), extract_loc(obj.get("jobLocation"))))
            return jobs
        for v in obj.values():
            jobs.extend(walk_for_jobs(v, source, depth+1))
    elif isinstance(obj, list):
        for item in obj:
            jobs.extend(walk_for_jobs(item, source, depth+1))
    return jobs


def enrich_job(job, timeout=12):
    if not job.get("link") or (job.get("company") and job.get("description")):
        return job
    try:
        html = fetch_page(job["link"], timeout)
        # Try __NEXT_DATA__
        nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if nd:
            data = json.loads(nd.group(1))
            jp = find_job_posting(data)
            if jp:
                org = jp.get("hiringOrganization", {})
                if not job["company"]: job["company"] = org.get("name", "") if isinstance(org, dict) else ""
                if not job["description"]: job["description"] = clean(jp.get("description", ""))
                if not job.get("date_posted"): job["date_posted"] = jp.get("datePosted", "")
                if not job.get("location"): job["location"] = extract_loc(jp.get("jobLocation"))
        # Fallback: JSON-LD
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                for item in (ld if isinstance(ld, list) else [ld]):
                    if item.get("@type") == "JobPosting":
                        org = item.get("hiringOrganization", {})
                        if not job["company"]: job["company"] = org.get("name", "") if isinstance(org, dict) else ""
                        if not job["description"]: job["description"] = clean(item.get("description", ""))
                        if not job.get("date_posted"): job["date_posted"] = item.get("datePosted", "")
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception as e:
        print(f"    [WARN] Enrich {job['id']}: {e}")
    return job


def find_job_posting(obj):
    if isinstance(obj, dict):
        if obj.get("@type") == "JobPosting": return obj
        for v in obj.values():
            r = find_job_posting(v)
            if r: return r
    elif isinstance(obj, list):
        for item in obj:
            r = find_job_posting(item)
            if r: return r
    return None

def extract_loc(loc):
    if isinstance(loc, dict):
        a = loc.get("address", {})
        return a.get("addressLocality", "") if isinstance(a, dict) else ""
    if isinstance(loc, list) and loc: return extract_loc(loc[0])
    return ""

def clean(t):
    if not t: return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(str(t)))).strip()[:500]

def make_job(title, link, company, desc, source, date_posted="", location=""):
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(str(title)))).strip()
    return {
        "id": hashlib.md5((title+link+company).encode()).hexdigest()[:12],
        "title": title, "company": company, "description": desc[:500] if desc else "",
        "link": link, "source": source, "date_posted": date_posted,
        "location": location or "Oslo",
    }

def deduplicate(jobs):
    unique, seen = [], set()
    for j in jobs:
        lk = re.sub(r"[?#].*", "", j.get("link", ""))
        if lk and lk in seen: continue
        if lk: seen.add(lk)
        tw = norm(j["title"])
        if not any(len(tw & norm(e["title"])) / max(len(tw), len(norm(e["title"])), 1) > 0.7 for e in unique):
            unique.append(j)
    return unique

def norm(t):
    t = re.sub(r"[^a-zæøå0-9\s]", "", t.lower())
    return {w for w in t.split() if w not in {"i","og","for","til","en","et","vi","the","a","an","in","to","and","of"} and len(w)>2}

def score_jobs(jobs, config):
    w = config["scoring"]; roles = config["role_keywords"]; skills = config["skill_keywords"]; cos = config["company_tiers"]
    boost = [s.lower() for s in config.get("seniority_boost", [])]; pen = [s.lower() for s in config.get("seniority_penalty", [])]
    for j in jobs:
        t = (j["title"]+" "+j.get("company","")+" "+j.get("description","")).lower()
        rs, mr = 0, []
        for kw in roles["tier1_perfect"]:
            if kw in t: rs=max(rs,10); mr.append(kw)
        for kw in roles["tier2_strong"]:
            if kw in t: rs=max(rs,7); mr.append(kw)
        for kw in roles["tier3_relevant"]:
            if kw in t: rs=max(rs,4); mr.append(kw)
        sh = sum(1 for s in skills if s.lower() in t); ss = min(10, sh*2)
        cs, ct = 0, ""
        cl = j.get("company","").lower()
        for c in cos["tier1_top"]:
            if c in cl or c in t: cs,ct = 10,"Topp-selskap"; break
        if not cs:
            for c in cos["tier2_strong"]:
                if c in cl or c in t: cs,ct = 7,"Sterkt selskap"; break
        if not cs:
            for c in cos["tier3_relevant"]:
                if c in cl or c in t: cs,ct = 4,"Relevant bransje"; break
        fs = 5
        if j.get("date_posted"):
            try:
                d = datetime.fromisoformat(j["date_posted"][:10]); days = (datetime.now()-d).days
                fs = 10 if days<=1 else 8 if days<=3 else 5 if days<=7 else 3 if days<=14 else 1
                j["days_old"] = days
            except (ValueError, TypeError): pass
        adj = 0
        for s in boost:
            if s in t: adj=1.0; break
        for s in pen:
            if s in t: adj=-1.5; break
        j["score"]=round(max(1,min(10, rs*w["role_match_weight"]+ss*w["skill_match_weight"]+cs*w["company_weight"]+fs*w["freshness_weight"]+adj)),1)
        j["role_score"]=rs; j["skill_score"]=ss; j["company_score"]=cs; j["company_tier"]=ct
        j["freshness_score"]=fs; j["matched_roles"]=list(set(mr))[:5]; j["skill_hits"]=sh
    return jobs

def categorize(jobs):
    for j in jobs:
        t = (j["title"]+" "+j.get("description","")).lower()
        if any(k in t for k in ["wealth","formue","private bank","privatbank"]): j["category"],j["category_color"]="Wealth / Private Banking","#185FA5"
        elif any(k in t for k in ["fund","fond","asset management","kapitalforvaltning"]): j["category"],j["category_color"]="Asset / Fund Management","#639922"
        elif any(k in t for k in ["sales","salg","client","klient","rådgiver","kundeansvarlug"]): j["category"],j["category_color"]="Client Advisory / Sales","#7F77DD"
        elif any(k in t for k in ["analyst","analytiker","risk","compliance"]): j["category"],j["category_color"]="Analyse / Risk","#D85A30"
        elif any(k in t for k in ["trainee","graduate","junior","nyutdannet"]): j["category"],j["category_color"]="Trainee / Graduate","#1D9E75"
        else: j["category"],j["category_color"]="Finans (generelt)","#888780"
    return jobs

def main():
    config = load_config()
    all_jobs = []
    print("=== Job Briefing v2 ===\n")
    for name, url in config["sources"].items():
        print(f"  [{name}]")
        all_jobs.extend(fetch_finn(url, name))
        time.sleep(1.5)
    print(f"\nRaw: {len(all_jobs)}")
    unique = deduplicate(all_jobs)
    print(f"Deduped: {len(unique)}")
    print("Enriching...")
    for i, j in enumerate(unique[:25]):
        unique[i] = enrich_job(j)
        if (i+1)%5==0: print(f"  {i+1}/{min(25,len(unique))}")
        time.sleep(0.8)
    scored = score_jobs(unique, config)
    top = sorted([j for j in categorize(scored) if j["score"]>=config.get("min_score",3)], key=lambda x:-x["score"])[:config.get("max_jobs",15)]
    cats = {}
    for j in top:
        c=j.get("category",""); cats.setdefault(c,{"count":0,"color":j.get("category_color","#888")}); cats[c]["count"]+=1
    cos = {}
    for j in top:
        c=j.get("company","");
        if c: cos[c]=cos.get(c,0)+1
    now = datetime.now(timezone.utc)
    output = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_date": now.strftime("%d. %B %Y").lower(),
        "summary": {"categories":cats, "avg_score":round(sum(j["score"] for j in top)/len(top),1) if top else 0, "top_companies":dict(sorted(cos.items(),key=lambda x:-x[1])[:5])},
        "jobs": [{"id":j["id"],"title":j["title"],"company":j.get("company",""),"description":j.get("description","")[:200],"link":j.get("link",""),"location":j.get("location","Oslo"),"score":j["score"],"category":j.get("category",""),"category_color":j.get("category_color","#888"),"company_tier":j.get("company_tier",""),"matched_roles":j.get("matched_roles",[]),"skill_hits":j.get("skill_hits",0),"days_old":j.get("days_old"),"date_posted":j.get("date_posted","")} for j in top],
        "stats": {"total_fetched":len(all_jobs),"after_dedup":len(unique),"above_threshold":len([j for j in scored if j["score"]>=config.get("min_score",3)]),"published":len(top)},
    }
    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✓ {len(top)} jobs written")
    for c,d in cats.items(): print(f"  {c}: {d['count']}")

if __name__ == "__main__":
    main()
