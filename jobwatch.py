#!/usr/bin/env python3
"""jobwatch: pulls postings from public job-board APIs, screens them with Richard's rules,
and writes docs/index.html + docs/jobs.csv. No third-party packages needed.

Run:   python jobwatch.py           (live)
       python jobwatch.py --demo    (offline demo using sample_postings.json)
       python jobwatch.py --test    (offline rule checks)
"""
import csv, datetime as dt, html, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "jobwatch/1.0 (personal job search; contact: owner)"
TODAY = dt.date.today()

# ---------------------------------------------------------------- helpers
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def http(url, data=None, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers: h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    if body is not None: h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()

def norm(company, title, location, url, desc, posted="", pid="", source=""):
    return {"id": pid or url, "source": source, "company": company, "title": (title or "").strip(),
            "location": (location or "").strip(), "url": url, "posted": (posted or "")[:10],
            "description": desc or ""}

# ---------------------------------------------------------------- fetchers
def fetch_greenhouse(src):
    d = http(f"https://boards-api.greenhouse.io/v1/boards/{src['slug']}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        out.append(norm(src["name"], j.get("title"), (j.get("location") or {}).get("name"),
                        j.get("absolute_url"), strip_html(j.get("content")), j.get("updated_at"),
                        f"gh-{src['slug']}-{j.get('id')}", "greenhouse"))
    return out

def fetch_lever(src):
    d = http(f"https://api.lever.co/v0/postings/{src['slug']}?mode=json")
    out = []
    for j in d:
        cat = j.get("categories") or {}
        posted = ""
        if j.get("createdAt"):
            posted = dt.datetime.utcfromtimestamp(j["createdAt"] / 1000).strftime("%Y-%m-%d")
        loc = cat.get("location") or ""
        if j.get("workplaceType") == "remote": loc = (loc + " (Remote)").strip()
        out.append(norm(src["name"], j.get("text"), loc, j.get("hostedUrl"),
                        j.get("descriptionPlain") or strip_html(j.get("description")), posted,
                        f"lv-{src['slug']}-{j.get('id')}", "lever"))
    return out

def fetch_ashby(src):
    d = http(f"https://api.ashbyhq.com/posting-api/job-board/{src['slug']}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        loc = j.get("location") or ""
        if j.get("isRemote"): loc = (loc + " (Remote)").strip()
        comp = ""
        c = (j.get("compensation") or {}).get("compensationTierSummary")
        if c: comp = " Compensation: " + c
        out.append(norm(src["name"], j.get("title"), loc, j.get("jobUrl"),
                        (j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))) + comp,
                        j.get("publishedAt"), f"ab-{src['slug']}-{j.get('id')}", "ashby"))
    return out

def fetch_workday(src, terms, limit_detail=25):
    base = f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}"
    seen, out = set(), []
    for term in terms:
        d = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        for j in d.get("jobPostings", []):
            path = j.get("externalPath")
            if not path or path in seen: continue
            seen.add(path)
            if len(out) >= limit_detail: break
            try:
                info = http(base + path).get("jobPostingInfo", {})
            except Exception:
                info = {}
            loc = info.get("location") or j.get("locationsText") or ""
            if "remote" in (info.get("remoteType") or "").lower(): loc += " (Remote)"
            out.append(norm(src["name"], info.get("title") or j.get("title"), loc,
                            info.get("externalUrl") or f"https://{src['host']}/{src['site']}{path}",
                            strip_html(info.get("jobDescription")), info.get("startDate"),
                            f"wd-{src['tenant']}-{path}", "workday"))
            time.sleep(0.4)
    return out

def fetch_adzuna(cfg):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set (optional source)")
    out = []
    for q in cfg.get("queries", []):
        qs = urllib.parse.urlencode({"app_id": app_id, "app_key": app_key, "results_per_page": 50,
                                     "what": q, "where": "remote", "max_days_old": cfg.get("max_days_old", 5),
                                     "content-type": "application/json"})
        d = http(f"https://api.adzuna.com/v1/api/jobs/{cfg.get('country','us')}/search/1?{qs}")
        for j in d.get("results", []):
            comp = ""
            if j.get("salary_min"): comp = f" Compensation: ${int(j['salary_min']):,} - ${int(j.get('salary_max') or j['salary_min']):,}"
            out.append(norm((j.get("company") or {}).get("display_name", "?"), j.get("title"),
                            ((j.get("location") or {}).get("display_name") or "") + " (Remote)",
                            j.get("redirect_url"), strip_html(j.get("description")) + comp,
                            j.get("created"), f"az-{j.get('id')}", "adzuna"))
        time.sleep(0.5)
    return out

# ---------------------------------------------------------------- screening rules
def has(term, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None

SKIP_VERTICALS = {  # name: (terms, hits needed to skip)
    "GTM / sales finance": (["pipeline", "bookings", "arr", "cac", "quota", "rep ramp", "deal desk", "revops", "net revenue retention", "pipe-to-spend", "territory"], 2),
    "Bank / treasury finance": (["cet1", "rwa", "alm", "liquidity risk", "deposit modeling", "capital planning"], 2),
    "Cloud FinOps": (["finops", "aws cost", "cost tagging", "chargeback"], 1),
    "Insurance-specific finance": (["loss ratio", "combined ratio", "naic", "ifrs 17", "reserving", "medical loss ratio", "medical economics"], 1),
    "Corporate treasury / debt capital markets": (["treasury management", "debt capital markets", "cash pooling"], 1),
    "OCM / Prosci / ADKAR": (["prosci", "adkar"], 1),
    "Airport / municipal finance": (["airport rate", "municipal bond"], 1),
    "Technical accounting": (["technical accounting", "asc 606", "sec reporting", "gaap reporting"], 2),
    "Clearance / citizenship": (["security clearance", "u.s. citizens only", "u.s. citizenship required", "must be a u.s. citizen"], 1),
}
PLATFORMS = ["sap", "netsuite", "anaplan", "adaptive", "hyperion", "oracle", "workday", "capital iq", "pitchbook", "epic"]

def parse_comp(text):
    nums = []
    for m in re.finditer(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK])", text):
        raw = m.group(1).replace(",", "").lower().replace(" ", "")
        try:
            v = float(raw[:-1]) * 1000 if raw.endswith("k") else float(raw)
        except ValueError:
            continue
        if 40000 <= v <= 600000: nums.append(v)
    return (min(nums), max(nums)) if nums else (None, None)

def lane_of(title):
    t = title.lower()
    if any(k in t for k in ["strategy", "strategic planning", "corporate development", "consultant", "transformation"]): return "Strategy / Consulting"
    return "FP&A / Finance"

def screen(p, cfg):
    prof = cfg["profile"]
    title, loc = p["title"], p["location"]
    text = (title + " " + loc + " " + p["description"]).lower()
    skips, flags, notes = [], [], []
    tl = title.lower()

    if not any(k in tl for k in cfg["title_include"]): skips.append("Title is outside target functions")
    ex = [k for k in cfg["title_exclude"] if k in tl]
    if ex: skips.append(f"Title excluded: {ex[0].strip()}")

    for name, (terms, need) in SKIP_VERTICALS.items():
        hit = [t for t in terms if has(t, text)]
        if len(hit) >= need: skips.append(f"{name}: '{', '.join(hit[:3])}'")

    remote = bool(re.search(r"\bremote\b|work from home|work at home|telecommute", (loc + " " + p["description"][:1500]).lower()))
    onsite = bool(re.search(r"\b(hybrid|on-?site|in-office|in office)\b", (loc + " " + p["description"][:1500]).lower()))
    if onsite and not remote:
        if not any(m in text for m in prof["accepted_metros"]):
            skips.append("On-site or hybrid outside accepted metros")
    elif onsite and remote:
        flags.append("Mentions both remote and hybrid/on-site: confirm")
    if re.search(r"(must|required to|need to) (live|reside)|reside in or be in a commutable|commutable distance", text):
        flags.append("Residency or commute requirement: check it covers Florida")

    lane = lane_of(title)
    floor = prof["pay_floor_strategy"] if lane.startswith("Strategy") else prof["pay_floor_fpa"]
    lo, hi = parse_comp(p["description"] + " " + loc)
    if hi is not None and hi < floor: skips.append(f"Pay tops out at ${hi:,.0f}, below ${floor:,.0f} floor")
    elif lo is not None and lo < floor <= hi: flags.append(f"Pay range ${lo:,.0f}-${hi:,.0f} straddles ${floor:,.0f} floor")
    elif lo is None: flags.append("Pay not posted")

    yrs = [int(x) for x in re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years", text)]
    if yrs:
        need = max(y for y in yrs if y <= 20) if any(y <= 20 for y in yrs) else 0
        gap = need - prof["years_experience"]
        if gap >= 3.5: flags.append(f"Experience gap: asks {need}+ years vs {prof['years_experience']}")
        elif gap >= 1.5: flags.append(f"Minor experience gap: {need}+ years")
    for m in re.finditer(r"(?:up to|travel[^.]{0,30}?)\s*(\d{2,3})\s*%", text):
        if int(m.group(1)) > prof["max_travel_percent"]: flags.append(f"Travel up to {m.group(1)}%"); break
    if re.search(r"direct reports?|manage a team of|people manage|performance management of", text):
        flags.append("People-management language: read the posting")
    if has("sql", text): flags.append("SQL mentioned: check if required or preferred")
    if has("actuarial", text): flags.append("Actuarial / insurance context")
    if has("medicare advantage", text): flags.append("Medicare Advantage knowledge expected")
    plat = [x for x in PLATFORMS if has(x, text)]
    if plat: notes.append("Named platforms (note only, never a skip): " + ", ".join(plat[:4]))
    if re.match(r"(associate|analyst|jr|junior)\b", tl): flags.append("Possible down-level title")
    if any(c in p["company"].lower() for c in cfg["exposure_companies"]):
        flags.append("Optum-family company: exposure check")

    health = any(has(k, text) for k in cfg["healthcare_words"])
    verdict = "SKIP" if skips else ("REVIEW" if flags else "GO")
    return {"verdict": verdict, "lane": lane, "skips": skips, "flags": flags, "notes": notes, "healthcare": health,
            "pay": (f"${lo:,.0f}-${hi:,.0f}" if lo and hi and lo != hi else (f"${lo:,.0f}" if lo else ""))}

# ---------------------------------------------------------------- run
def collect(cfg):
    src, jobs, health = cfg["sources"], [], []
    def run(label, fn):
        try:
            got = fn(); jobs.extend(got); health.append((label, "ok", f"{len(got)} postings"))
        except urllib.error.HTTPError as e:
            health.append((label, "FAILED", f"HTTP {e.code}: check the slug or host in config.json"))
        except Exception as e:
            health.append((label, "FAILED", str(e)[:120]))
    for s in src.get("greenhouse", []): run("Greenhouse: " + s["name"], lambda s=s: fetch_greenhouse(s))
    for s in src.get("lever", []):
        if s["slug"] != "replace-me": run("Lever: " + s["name"], lambda s=s: fetch_lever(s))
    for s in src.get("ashby", []):
        if s["slug"] != "replace-me": run("Ashby: " + s["name"], lambda s=s: fetch_ashby(s))
    for s in src.get("workday", []): run("Workday: " + s["name"], lambda s=s: fetch_workday(s, src.get("workday_search_terms", ["finance"])))
    if src.get("adzuna", {}).get("enabled"): run("Adzuna search", lambda: fetch_adzuna(src["adzuna"]))
    return jobs, health

def too_old(p, max_days):
    try:
        return (TODAY - dt.date.fromisoformat(p["posted"])).days > max_days if p["posted"] else False
    except ValueError:
        return False

def build(cfg, jobs, health, seen, out_dir, demo=False):
    rows, seen_ids = [], set()
    for p in jobs:
        if p["id"] in seen_ids or too_old(p, cfg["profile"]["max_age_days"]): continue
        seen_ids.add(p["id"])
        r = screen(p, cfg)
        r.update({"id": p["id"], "company": p["company"], "title": p["title"], "location": p["location"],
                  "url": p["url"], "posted": p["posted"], "new": p["id"] not in seen})
        rows.append(r)
    order = {"GO": 0, "REVIEW": 1, "SKIP": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], not r["new"], not r["healthcare"], r["company"]))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "jobs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["verdict", "new", "company", "title", "location", "pay", "lane", "why", "url", "posted"])
        for r in rows: w.writerow([r["verdict"], "NEW" if r["new"] else "", r["company"], r["title"], r["location"], r["pay"], r["lane"], " | ".join(r["skips"] + r["flags"] + r["notes"]), r["url"], r["posted"]])
    data = json.dumps(rows).replace("</", "<\\/")
    hl = "".join(f"<tr><td>{html.escape(a)}</td><td class='{'bad' if b != 'ok' else 'ok'}'>{b}</td><td>{html.escape(c)}</td></tr>" for a, b, c in health)
    page = PAGE.replace("__DATA__", data).replace("__HEALTH__", hl).replace("__STAMP__", f"{'DEMO DATA, ' if demo else ''}updated {dt.datetime.now().strftime('%b %d, %Y %I:%M %p')}")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f: f.write(page)
    return rows

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Job Watch</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:Arial,sans-serif;margin:20px;color:#222}h1{margin:0 0 4px}.sub{color:#666;margin-bottom:14px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}input,select{padding:7px;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ddd;padding:7px;vertical-align:top;text-align:left}
th{background:#1f3864;color:#fff;position:sticky;top:0}.GO{background:#e6f4ea}.REVIEW{background:#fff8e1}.SKIP{background:#f5f5f5;color:#777}
.tag{font-weight:bold}.new{background:#d32f2f;color:#fff;border-radius:3px;padding:1px 5px;font-size:11px;margin-right:4px}
.why{font-size:12px;color:#444}.bad{color:#b71c1c;font-weight:bold}.ok{color:#2e7d32}a{color:#1a56c9}</style></head><body>
<h1>Job Watch</h1><div class="sub">__STAMP__</div>
<div class="bar"><input id="q" placeholder="Search company or title" oninput="draw()">
<select id="v" onchange="draw()"><option value="">All verdicts</option><option>GO</option><option>REVIEW</option><option>SKIP</option></select>
<label><input type="checkbox" id="n" onchange="draw()"> New only</label>
<label><input type="checkbox" id="h" onchange="draw()"> Healthcare only</label>
<label><input type="checkbox" id="s" onchange="draw()" checked> Hide SKIP</label><span id="c"></span></div>
<table><thead><tr><th>Verdict</th><th>Company</th><th>Title</th><th>Location</th><th>Pay</th><th>Why</th><th>Posted</th></tr></thead><tbody id="b"></tbody></table>
<h3>Source health</h3><table style="width:auto"><tr><th>Source</th><th>Status</th><th>Detail</th></tr>__HEALTH__</table>
<script>const D=__DATA__;function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function draw(){const q=document.getElementById('q').value.toLowerCase(),v=document.getElementById('v').value,n=document.getElementById('n').checked,h=document.getElementById('h').checked,s=document.getElementById('s').checked;
const rows=D.filter(r=>(!q||(r.company+' '+r.title).toLowerCase().includes(q))&&(!v||r.verdict==v)&&(!n||r.new)&&(!h||r.healthcare)&&(!s||r.verdict!='SKIP'));
document.getElementById('c').textContent=rows.length+' shown of '+D.length;
document.getElementById('b').innerHTML=rows.map(r=>`<tr class="${r.verdict}"><td><span class="tag">${r.verdict}</span></td><td>${esc(r.company)}</td><td>${r.new?'<span class="new">NEW</span>':''}<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a></td><td>${esc(r.location)}</td><td>${esc(r.pay)}</td><td class="why">${esc(r.skips.concat(r.flags).concat(r.notes).join(' | '))}</td><td>${esc(r.posted)}</td></tr>`).join('')}draw();</script></body></html>"""

def main():
    cfg = load_json(os.path.join(HERE, "config.json"))
    out_dir = os.path.join(HERE, "docs")
    if "--test" in sys.argv: return selftest(cfg)
    if "--demo" in sys.argv:
        jobs = load_json(os.path.join(HERE, "sample_postings.json"))
        rows = build(cfg, jobs, [("Demo data", "ok", f"{len(jobs)} sample postings")], {}, out_dir, demo=True)
        for r in rows: print(f"{r['verdict']:6} {r['company']:28} {r['title'][:46]:46} {' | '.join(r['skips'] + r['flags'] + r['notes'])[:90]}")
        return
    seen_path = os.path.join(HERE, "data", "seen.json")
    seen = load_json(seen_path, {})
    jobs, health = collect(cfg)
    rows = build(cfg, jobs, health, seen, out_dir)
    for r in rows: seen.setdefault(r["id"], TODAY.isoformat())
    os.makedirs(os.path.dirname(seen_path), exist_ok=True)
    with open(seen_path, "w", encoding="utf-8") as f: json.dump(seen, f)
    print(f"{len(rows)} postings screened; {sum(r['verdict']=='GO' for r in rows)} GO, {sum(r['verdict']=='REVIEW' for r in rows)} REVIEW")
    for h in health: print(h)

def selftest(cfg):
    jobs = load_json(os.path.join(HERE, "sample_postings.json"))
    got = {p["id"]: screen(p, cfg)["verdict"] for p in jobs}
    expect = {"d1": "GO", "d2": "SKIP", "d3": "SKIP", "d4": "SKIP", "d5": "REVIEW", "d6": "REVIEW", "d7": "REVIEW", "d8": "SKIP"}
    bad = {k: (got[k], expect[k]) for k in expect if got[k] != expect[k]}
    print("self-test:", "PASS" if not bad else f"MISMATCH {bad}"); print(got)
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main() or 0)
