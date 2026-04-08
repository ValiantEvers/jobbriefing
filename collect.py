#!/usr/bin/env python3
"""
Job Briefing — Collector & Scorer
Scrapes Finn.no job listings, scores against profile, outputs jobs.json
"""

import json
import re
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from html import unescape

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ──────────────────────────────────────────────
# Finn.no Scraper
# ──────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5",
}


def fetch_finn_search(url, source_name, timeout=15):
    """Fetch Finn.no search results page and extract job listings."""
    jobs = []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract job cards from Finn.no search results HTML
        # Finn uses article tags or specific class patterns for job cards
        jobs.extend(_parse_finn_html(html, source_name))

    except Exception as e:
        print(f"  [WARN] Failed to fetch {source_name}: {e}")

    return jobs


def _parse_finn_html(html, source_name):
    """Parse Finn.no HTML search results for job listings."""
    jobs = []

    # Strategy 1: Look for structured data (JSON-LD)
    json_ld_pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
    for match in re.finditer(json_ld_pattern, html, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                for item in data.get("itemListElement", []):
                    entity = item.get("item", item)
                    title = entity.get("title") or entity.get("name", "")
                    url = entity.get("url", "")
                    if title:
                        jobs.append(_make_job(title, url, "", "", source_name))
            elif isinstance(data, list):
                for item in data:
                    if item.get("@type") in ("JobPosting", "ListItem"):
                        title = item.get("title") or item.get("name", "")
                        url = item.get("url", "")
                        org = ""
                        if isinstance(item.get("hiringOrganization"), dict):
                            org = item["hiringOrganization"].get("name", "")
                        if title:
                            jobs.append(_make_job(title, url, org, "", source_name))
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 2: Parse ad links from HTML
    # Finn.no job ads typically link to /job/fulltime/ad.html?finnkode=XXXXX
    ad_pattern = r'<a[^>]*href="(https://www\.finn\.no/job/[^"]*finnkode=\d+[^"]*)"[^>]*>(.*?)</a>'
    for match in re.finditer(ad_pattern, html, re.DOTALL):
        link = match.group(1)
        inner = re.sub(r"<[^>]+>", " ", match.group(2)).strip()
        inner = re.sub(r"\s+", " ", inner)
        if inner and len(inner) > 5 and len(inner) < 200:
            # Avoid duplicates
            if not any(j["link"] == link for j in jobs):
                jobs.append(_make_job(inner, link, "", "", source_name))

    # Strategy 3: Broader pattern for job titles
    # Look for patterns like title + company in typical Finn card markup
    card_pattern = r'(?:class="[^"]*(?:job|ad|result)[^"]*"[^>]*>)\s*(?:<[^>]+>)*\s*([^<]{10,120})'
    if not jobs:
        for match in re.finditer(card_pattern, html):
            text = match.group(1).strip()
            text = unescape(text)
            if _looks_like_job_title(text):
                jobs.append(_make_job(text, "", "", "", source_name))

    return jobs


def _looks_like_job_title(text):
    """Heuristic: does this text look like a job title?"""
    job_words = [
        "rådgiver", "analyst", "manager", "advisor", "associate",
        "trainee", "graduate", "leder", "konsulent", "spesialist",
        "medarbeider", "koordinator", "controller", "director",
        "stilling", "ansvarlig", "senior", "junior"
    ]
    text_lower = text.lower()
    return any(w in text_lower for w in job_words)


def _make_job(title, link, company, description, source):
    """Create a standardized job dict."""
    title = re.sub(r"<[^>]+>", "", unescape(title)).strip()
    title = re.sub(r"\s+", " ", title)
    job_id = hashlib.md5((title + link).encode()).hexdigest()[:12]
    return {
        "id": job_id,
        "title": title,
        "company": company,
        "description": description,
        "link": link,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

# ──────────────────────────────────────────────
# Fetch individual job details
# ──────────────────────────────────────────────

def enrich_job(job, timeout=10):
    """Fetch the individual job page to extract company, description, location, date."""
    if not job.get("link"):
        return job
    try:
        req = urllib.request.Request(job["link"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Try JSON-LD first
        json_ld = re.search(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        if json_ld:
            try:
                data = json.loads(json_ld.group(1))
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "JobPosting"), data[0] if data else {})
                if data.get("@type") == "JobPosting":
                    job["title"] = data.get("title", job["title"])
                    job["description"] = _clean_html(data.get("description", ""))[:500]
                    if isinstance(data.get("hiringOrganization"), dict):
                        job["company"] = data["hiringOrganization"].get("name", job["company"])
                    if data.get("datePosted"):
                        job["date_posted"] = data["datePosted"]
                    loc = data.get("jobLocation", {})
                    if isinstance(loc, dict):
                        addr = loc.get("address", {})
                        if isinstance(addr, dict):
                            job["location"] = addr.get("addressLocality", "")
                    elif isinstance(loc, list) and loc:
                        addr = loc[0].get("address", {})
                        if isinstance(addr, dict):
                            job["location"] = addr.get("addressLocality", "")
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # Fallback: extract from meta tags
        if not job.get("company"):
            og_site = re.search(r'<meta[^>]*property="og:site_name"[^>]*content="([^"]*)"', html)
            if og_site:
                job["company"] = unescape(og_site.group(1))

        if not job.get("description"):
            og_desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
            if og_desc:
                job["description"] = unescape(og_desc.group(1))[:500]

    except Exception as e:
        print(f"    [WARN] Could not enrich {job['id']}: {e}")

    return job


def _clean_html(text):
    """Remove HTML tags and clean whitespace."""
    text = re.sub(r"<[^>]+>", " ", unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ──────────────────────────────────────────────
# Deduplication
# ──────────────────────────────────────────────

def deduplicate(jobs):
    """Remove duplicate job postings based on title + company similarity."""
    unique = []
    seen_links = set()

    for job in jobs:
        # Dedup by link
        if job.get("link"):
            link_key = re.sub(r"[?#].*", "", job["link"])
            if link_key in seen_links:
                continue
            seen_links.add(link_key)

        # Dedup by title similarity
        title_words = _normalize(job["title"])
        is_dup = False
        for existing in unique:
            existing_words = _normalize(existing["title"])
            overlap = len(title_words & existing_words)
            max_len = max(len(title_words), len(existing_words), 1)
            if overlap / max_len > 0.7:
                is_dup = True
                break

        if not is_dup:
            unique.append(job)

    return unique


def _normalize(text):
    """Normalize to set of lowercase words."""
    text = re.sub(r"[^a-zæøå0-9\s]", "", text.lower())
    stop = {"i", "og", "for", "til", "en", "et", "vi", "du", "the", "a", "an", "in", "to", "and", "of", "or"}
    return {w for w in text.split() if w not in stop and len(w) > 2}

# ──────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────

def score_jobs(jobs, config):
    """Score each job on 1-10 scale based on profile match."""
    weights = config["scoring"]
    roles = config["role_keywords"]
    skills = config["skill_keywords"]
    companies = config["company_tiers"]
    seniority_boost = [s.lower() for s in config.get("seniority_boost", [])]
    seniority_penalty = [s.lower() for s in config.get("seniority_penalty", [])]

    for job in jobs:
        text = (
            job["title"] + " " +
            job.get("company", "") + " " +
            job.get("description", "")
        ).lower()

        # 1) Role match (0-10)
        role_score = 0
        matched_roles = []
        for kw in roles["tier1_perfect"]:
            if kw in text:
                role_score = max(role_score, 10)
                matched_roles.append(kw)
        for kw in roles["tier2_strong"]:
            if kw in text:
                role_score = max(role_score, 7)
                matched_roles.append(kw)
        for kw in roles["tier3_relevant"]:
            if kw in text:
                role_score = max(role_score, 4)
                matched_roles.append(kw)

        # 2) Skill match (0-10)
        skill_hits = sum(1 for s in skills if s.lower() in text)
        skill_score = min(10, skill_hits * 2)
        job["skill_hits"] = skill_hits

        # 3) Company quality (0-10)
        company_score = 0
        company_tier = ""
        company_lower = job.get("company", "").lower()
        for c in companies["tier1_top"]:
            if c in company_lower or c in text:
                company_score = 10
                company_tier = "Topp-selskap"
                break
        if company_score == 0:
            for c in companies["tier2_strong"]:
                if c in company_lower or c in text:
                    company_score = 7
                    company_tier = "Sterkt selskap"
                    break
        if company_score == 0:
            for c in companies["tier3_relevant"]:
                if c in company_lower or c in text:
                    company_score = 4
                    company_tier = "Relevant bransje"
                    break

        # 4) Freshness (0-10)
        freshness_score = 5  # default
        if job.get("date_posted"):
            try:
                posted = datetime.fromisoformat(job["date_posted"].replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                days_old = (now - posted).days
                if days_old <= 1:
                    freshness_score = 10
                elif days_old <= 3:
                    freshness_score = 8
                elif days_old <= 7:
                    freshness_score = 5
                elif days_old <= 14:
                    freshness_score = 3
                else:
                    freshness_score = 1
                job["days_old"] = days_old
            except (ValueError, TypeError):
                pass

        # Seniority adjustment
        seniority_adj = 0
        for s in seniority_boost:
            if s in text:
                seniority_adj = 1.0
                break
        for s in seniority_penalty:
            if s in text:
                seniority_adj = -1.5
                break

        # Weighted total
        total = (
            role_score * weights["role_match_weight"]
            + skill_score * weights["skill_match_weight"]
            + company_score * weights["company_weight"]
            + freshness_score * weights["freshness_weight"]
            + seniority_adj
        )

        job["score"] = round(max(1, min(10, total)), 1)
        job["role_score"] = role_score
        job["skill_score"] = skill_score
        job["company_score"] = company_score
        job["company_tier"] = company_tier
        job["freshness_score"] = freshness_score
        job["matched_roles"] = list(set(matched_roles))[:5]

    return jobs

# ──────────────────────────────────────────────
# Categorize
# ──────────────────────────────────────────────

def categorize_jobs(jobs):
    """Assign a primary category to each job."""
    for job in jobs:
        text = (job["title"] + " " + job.get("description", "")).lower()
        if any(k in text for k in ["wealth", "formue", "private bank", "privatbank"]):
            job["category"] = "Wealth / Private Banking"
            job["category_color"] = "#185FA5"
        elif any(k in text for k in ["fund", "fond", "asset management", "kapitalforvaltning"]):
            job["category"] = "Asset / Fund Management"
            job["category_color"] = "#639922"
        elif any(k in text for k in ["sales", "salg", "client", "klient", "rådgiver"]):
            job["category"] = "Client Advisory / Sales"
            job["category_color"] = "#7F77DD"
        elif any(k in text for k in ["analyst", "analytiker", "risk", "compliance"]):
            job["category"] = "Analyse / Risk"
            job["category_color"] = "#D85A30"
        elif any(k in text for k in ["trainee", "graduate", "junior", "nyutdannet"]):
            job["category"] = "Trainee / Graduate"
            job["category_color"] = "#1D9E75"
        else:
            job["category"] = "Finans (generelt)"
            job["category_color"] = "#888780"
    return jobs

# ──────────────────────────────────────────────
# Summary stats
# ──────────────────────────────────────────────

def compute_summary(jobs):
    """Compute category counts and other stats."""
    categories = {}
    for job in jobs:
        cat = job.get("category", "Annet")
        if cat not in categories:
            categories[cat] = {"count": 0, "color": job.get("category_color", "#888")}
        categories[cat]["count"] += 1

    avg_score = sum(j["score"] for j in jobs) / len(jobs) if jobs else 0
    top_companies = {}
    for j in jobs:
        c = j.get("company", "")
        if c:
            top_companies[c] = top_companies.get(c, 0) + 1

    return {
        "categories": categories,
        "avg_score": round(avg_score, 1),
        "top_companies": dict(sorted(top_companies.items(), key=lambda x: -x[1])[:5]),
    }

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    config = load_config()
    all_jobs = []

    print("=== Job Briefing ===")
    print(f"Fetching from {len(config['sources'])} Finn.no searches...")

    # 1) Fetch all search result pages
    for name, url in config["sources"].items():
        print(f"  Fetching {name}...")
        jobs = fetch_finn_search(url, name)
        print(f"    → {len(jobs)} listings found")
        all_jobs.extend(jobs)

    print(f"\nTotal raw listings: {len(all_jobs)}")

    # 2) Deduplicate
    unique = deduplicate(all_jobs)
    print(f"After dedup: {len(unique)}")

    # 3) Enrich top candidates (fetch individual pages)
    print("Enriching listings with details...")
    enriched = 0
    for job in unique[:30]:  # Limit to avoid rate limiting
        job = enrich_job(job)
        enriched += 1
        if enriched % 5 == 0:
            print(f"  Enriched {enriched}/{min(30, len(unique))}...")

    # 4) Score
    scored = score_jobs(unique, config)

    # 5) Categorize
    categorized = categorize_jobs(scored)

    # 6) Filter & sort
    min_score = config.get("min_score", 3.0)
    max_jobs = config.get("max_jobs", 15)
    filtered = [j for j in categorized if j["score"] >= min_score]
    filtered.sort(key=lambda x: x["score"], reverse=True)
    top = filtered[:max_jobs]

    print(f"Above threshold ({min_score}): {len(filtered)}")
    print(f"Final selection: {len(top)}")

    # 7) Summary
    summary = compute_summary(top)
    now = datetime.now(timezone.utc)

    # 8) Build output
    output = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_date": now.strftime("%d. %B %Y").lower(),
        "summary": summary,
        "jobs": [
            {
                "id": j["id"],
                "title": j["title"],
                "company": j.get("company", ""),
                "description": j.get("description", "")[:200],
                "link": j.get("link", ""),
                "location": j.get("location", "Oslo"),
                "score": j["score"],
                "category": j.get("category", ""),
                "category_color": j.get("category_color", "#888"),
                "company_tier": j.get("company_tier", ""),
                "matched_roles": j.get("matched_roles", []),
                "skill_hits": j.get("skill_hits", 0),
                "days_old": j.get("days_old"),
                "date_posted": j.get("date_posted", ""),
            }
            for j in top
        ],
        "stats": {
            "total_fetched": len(all_jobs),
            "after_dedup": len(unique),
            "above_threshold": len(filtered),
            "published": len(top),
        },
    }

    # 9) Write
    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ jobs.json written with {len(top)} listings")
    for cat, data in summary["categories"].items():
        print(f"  {cat}: {data['count']}")


if __name__ == "__main__":
    main()
