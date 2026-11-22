"""WAICO tracker: collects English and Chinese news about the World AI
Cooperation Organization (世界人工智能合作组织), sorts it into categories, and
writes the results to docs/ for the dashboard and RSS feed.

Run:  python crawler.py             (normal run, looks back `lookback_days`)
      python crawler.py --backfill  (searches as far back as sources allow)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse
from xml.etree import ElementTree as ET

import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
DOCS = ROOT / "docs"
ITEMS_PATH = DOCS / "items.json"
DATA_JS_PATH = DOCS / "data.js"
FEED_PATH = DOCS / "feed.xml"

USER_AGENT = (
    "Mozilla/5.0 (compatible; waico-tracker/1.0; research news monitor)"
)
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")


def log(msg: str) -> None:
    print(msg, flush=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(TAG.sub(" ", html.unescape(value)))
    return SPACE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

FETCH_STATS = {"ok": 0, "failed": 0}


def fetch(url: str, tries: int = 2, pause: float = 4.0) -> requests.Response | None:
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
            if resp.status_code == 200:
                FETCH_STATS["ok"] += 1
                return resp
            log(f"    HTTP {resp.status_code} (attempt {attempt})")
        except requests.RequestException as exc:
            log(f"    {type(exc).__name__}: {exc} (attempt {attempt})")
        if attempt < tries:
            time.sleep(pause)
    FETCH_STATS["failed"] += 1
    return None


def child_text(el: ET.Element, local_name: str) -> str | None:
    """Text of the first child whose tag (ignoring namespace) is local_name."""
    for child in el:
        if child.tag.split("}")[-1] == local_name:
            return child.text
    return None


def parse_rss_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def google_edition_params(edition: str) -> str:
    lang, _, country = edition.partition("-")
    country = country or lang.upper()
    special = {"zh-CN": "CN:zh-Hans", "zh-TW": "TW:zh-Hant", "zh-HK": "HK:zh-Hant"}
    ceid = special.get(edition, f"{country}:{lang}")
    return f"hl={edition}&gl={country}&ceid={ceid}"


def parse_google_news(xml_text: str, edition: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        outlet = clean_text(item.findtext("source"))
        source_el = item.find("source")
        outlet_url = source_el.get("url") if source_el is not None else None
        # Google appends " - Outlet" to headlines; strip it.
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(f" - {outlet}")].strip()
        summary = clean_text(item.findtext("description"))
        # Google's "description" is usually just the headline + outlet again.
        if summary.replace(outlet, "").strip() in ("", title) or summary.startswith(title):
            summary = ""
        out.append({
            "title": title,
            "url": (item.findtext("link") or "").strip(),
            "outlet": outlet or (urlparse(outlet_url).netloc if outlet_url else ""),
            "published": parse_rss_date(item.findtext("pubDate")),
            "summary": summary,
            "via": f"google_news:{edition}",
        })
    return out


def google_news(searches: list[dict], days: int | None) -> list[dict]:
    results = []
    for s in searches:
        edition = s.get("edition", "en-US")
        q = s["query"] + (f" when:{days}d" if days else "")
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&{google_edition_params(edition)}"
        log(f"  Google News [{edition}]")
        resp = fetch(url)
        if resp is None:
            continue
        try:
            found = parse_google_news(resp.text, edition)
        except ET.ParseError as exc:
            log(f"    could not read feed: {exc}")
            continue
        log(f"    {len(found)} results")
        results.extend(found)
        time.sleep(1.5)
    return results


def unwrap_bing_link(link: str) -> str:
    """Bing wraps article links in a click tracker; pull out the real URL."""
    parsed = urlparse(link)
    if "bing.com" in parsed.netloc:
        real = parse_qs(parsed.query).get("url")
        if real:
            return real[0]
    return link


def parse_bing_news(xml_text: str, market: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        out.append({
            "title": clean_text(item.findtext("title")),
            "url": unwrap_bing_link((item.findtext("link") or "").strip()),
            "outlet": clean_text(child_text(item, "Source")),
            "published": parse_rss_date(item.findtext("pubDate")),
            "summary": clean_text(item.findtext("description")),
            "via": f"bing_news:{market}",
        })
    return out


def bing_news(searches: list[dict], days: int | None) -> list[dict]:
    results = []
    for s in searches:
        market = s.get("market", "en-US")
        # Bing's freshness filter: interval 7 = past week, 8 = past month.
        fresh = ""
        if days:
            fresh = "&qft=" + quote_plus('interval="7"' if days <= 7 else 'interval="8"')
        url = (f"https://www.bing.com/news/search?q={quote_plus(s['query'])}"
               f"&format=rss&mkt={market}&setlang={market}{fresh}")
        log(f"  Bing News [{market}]")
        resp = fetch(url)
        if resp is None:
            continue
        try:
            found = parse_bing_news(resp.text, market)
        except ET.ParseError as exc:
            log(f"    could not read feed: {exc}")
            continue
        log(f"    {len(found)} results")
        results.extend(found)
        time.sleep(1.5)
    return results


def parse_gdelt(payload: dict) -> list[dict]:
    out = []
    for art in payload.get("articles", []) or []:
        seen = art.get("seendate")
        try:
            published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            published = None
        out.append({
            "title": clean_text(art.get("title")),
            "url": art.get("url", ""),
            "outlet": art.get("domain", ""),
            "published": published,
            "summary": "",
            "via": "gdelt",
            "gdelt_language": art.get("language", ""),
        })
    return out


def gdelt(searches: list[dict], days: int | None) -> list[dict]:
    results = []
    span = f"{days}d" if days else "3months"
    for s in searches:
        url = ("https://api.gdeltproject.org/api/v2/doc/doc"
               f"?query={quote_plus(s['query'])}&mode=artlist&format=json"
               f"&maxrecords=250&sort=datedesc&timespan={span}")
        log("  GDELT")
        time.sleep(6)  # GDELT asks for at most one request every 5 seconds
        resp = fetch(url, pause=8)
        if resp is None:
            continue
        try:
            payload = resp.json()
        except ValueError:
            log(f"    GDELT returned a message instead of data: {resp.text[:160]!r}")
            continue
        found = parse_gdelt(payload)
        log(f"    {len(found)} results")
        results.extend(found)
    return results


# --------------------------------------------------------------------------
# Relevance, language and categories
# --------------------------------------------------------------------------

def keyword_pattern(keyword: str) -> re.Pattern | None:
    kw = keyword.strip()
    if not kw:
        return None
    if CJK.search(kw):
        return re.compile(re.escape(kw))
    stem = kw.endswith("*")
    body = re.escape(kw.rstrip("*"))
    # Short all-caps terms (US, EU, UN, WAIC, WAICO) must match in capitals.
    letters = re.sub(r"[^A-Za-z]", "", kw)
    flags = 0 if (letters.isupper() and len(letters) <= 6) else re.IGNORECASE
    tail = "" if stem else r"(?![\w])"
    return re.compile(rf"(?<![\w]){body}{tail}", flags)


class Categorizer:
    def __init__(self, categories: list[dict]):
        self.categories = []
        for cat in categories:
            patterns = [(kw, keyword_pattern(str(kw))) for kw in cat.get("keywords", [])]
            self.categories.append((cat["id"], [(kw, p) for kw, p in patterns if p]))

    def __call__(self, text: str) -> tuple[list[str], dict[str, list[str]]]:
        hits: dict[str, list[str]] = {}
        for cat_id, patterns in self.categories:
            found = [str(kw) for kw, p in patterns if p.search(text)]
            if found:
                hits[cat_id] = found
        order = [c for c, _ in self.categories]
        ranked = sorted(hits, key=lambda c: (-len(hits[c]), order.index(c)))
        return (ranked or ["other"]), hits


class term_matcher:
    """Matches any of a list of terms, using the same rules as category keywords."""

    def __init__(self, terms: list[str]):
        self.patterns = [p for p in (keyword_pattern(str(t)) for t in terms) if p]

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def search(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)


def detect_language(title: str, summary: str, gdelt_language: str = "") -> str:
    text = f"{title} {summary}"
    if len(CJK.findall(text)) >= 2:
        return "zh"
    if gdelt_language and gdelt_language.lower() not in ("english", "chinese"):
        return "other"
    letters = re.findall(r"[^\W\d_]", text)
    if letters and sum(c.isascii() for c in letters) / len(letters) > 0.9:
        return "en"
    return "other"


def dedupe_key(title: str) -> str:
    return re.sub(r"[\W_]+", "", title.lower())


def make_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_store() -> list[dict]:
    if ITEMS_PATH.exists():
        try:
            return json.loads(ITEMS_PATH.read_text("utf-8")).get("items", [])
        except (ValueError, AttributeError):
            log("Warning: docs/items.json could not be read; starting a new archive.")
    return []


def merge(store: list[dict], raw: list[dict], cfg: dict, run_time: datetime) -> list[dict]:
    match = term_matcher(cfg.get("match_terms", []))
    exclude = term_matcher(cfg.get("exclude_terms", []) or [])
    context = term_matcher(cfg.get("context_terms", []) or [])
    by_key = {dedupe_key(it["title"]): it for it in store}
    by_url = {it["url"]: it for it in store if it.get("url")}
    new_items = []

    for r in raw:
        title = r["title"]
        if not title or not r["url"]:
            continue
        text = f"{title} {r['summary']}"
        if exclude and exclude.search(text):
            continue
        named = bool(match) and match.search(text)
        if not named and context and not context.search(text):
            continue  # WAICO not named and no sign the article is about AI
        key = dedupe_key(title)
        existing = by_key.get(key) or by_url.get(r["url"])
        if existing:
            if r["outlet"] and r["outlet"] not in existing["outlets"]:
                existing["outlets"].append(r["outlet"])
            if r["via"] not in existing["found_via"]:
                existing["found_via"].append(r["via"])
            if not existing.get("summary") and r["summary"]:
                existing["summary"] = r["summary"]
            # Prefer a direct article link over a Google News redirect.
            if "news.google.com" in existing["url"] and "news.google.com" not in r["url"]:
                existing["url"] = r["url"]
                by_url[r["url"]] = existing
            continue

        if match and match.search(title):
            focus = "headline"
        elif match and match.search(r["summary"]):
            focus = "text"
        else:
            focus = "search"  # the search engine matched the article body
        item = {
            "id": make_id(key),
            "title": title,
            "url": r["url"],
            "outlet": r["outlet"],
            "outlets": [r["outlet"]] if r["outlet"] else [],
            "published": iso(r["published"]),
            "first_seen": iso(run_time),
            "lang": detect_language(title, r["summary"], r.get("gdelt_language", "")),
            "summary": r["summary"][:400],
            "focus": focus,
            "found_via": [r["via"]],
        }
        store.append(item)
        by_key[key] = item
        by_url[item["url"]] = item
        new_items.append(item)
    return new_items


def categorize_all(store: list[dict], cfg: dict) -> None:
    categorize = Categorizer(cfg.get("categories", []))
    for item in store:
        cats, hits = categorize(f"{item['title']} {item.get('summary', '')}")
        item["categories"] = cats
        item["matched"] = hits


def sort_key(item: dict) -> str:
    return item.get("published") or item.get("first_seen") or ""


def write_outputs(store: list[dict], cfg: dict, run_time: datetime, stats: dict) -> None:
    DOCS.mkdir(exist_ok=True)
    store.sort(key=sort_key, reverse=True)
    categories = [
        {"id": c["id"], "label": c.get("label", c["id"]), "label_zh": c.get("label_zh", "")}
        for c in cfg.get("categories", [])
    ] + [{"id": "other", "label": "Other", "label_zh": "其他"}]
    payload = {
        "generated": iso(run_time),
        "stats": stats,
        "categories": categories,
        "items": store,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=1)
    ITEMS_PATH.write_text(body + "\n", "utf-8")
    DATA_JS_PATH.write_text(f"window.WAICO = {body};\n", "utf-8")
    write_feed(store[:100], categories, run_time)


def write_feed(items: list[dict], categories: list[dict], run_time: datetime) -> None:
    labels = {c["id"]: c["label"] for c in categories}
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = "WAICO tracker · 世界人工智能合作组织动态"
    ET.SubElement(ch, "link").text = os.environ.get("TRACKER_URL", "https://github.com/")
    ET.SubElement(ch, "description").text = (
        "English and Chinese news about the World AI Cooperation Organization")
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(run_time)
    for it in items:
        entry = ET.SubElement(ch, "item")
        prefix = {"zh": "[中文] ", "other": "[Other] "}.get(it["lang"], "")
        ET.SubElement(entry, "title").text = prefix + it["title"]
        ET.SubElement(entry, "link").text = it["url"]
        ET.SubElement(entry, "guid", isPermaLink="false").text = it["id"]
        when = parse_iso(it.get("published")) or parse_iso(it.get("first_seen"))
        if when:
            ET.SubElement(entry, "pubDate").text = format_datetime(when)
        outlets = ", ".join(it.get("outlets", [])) or it.get("outlet", "")
        desc = f"{outlets}. {it.get('summary', '')}".strip(". ")
        ET.SubElement(entry, "description").text = desc
        for cat in it.get("categories", []):
            ET.SubElement(entry, "category").text = labels.get(cat, cat)
    ET.indent(rss)
    FEED_PATH.write_bytes(ET.tostring(rss, encoding="utf-8", xml_declaration=True))


def write_job_summary(new_items: list[dict], stats: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [f"## WAICO tracker: {len(new_items)} new article(s)", ""]
    lines.append(" · ".join(f"{k}: {v}" for k, v in stats["by_source"].items()))
    lines.append("")
    for it in sorted(new_items, key=sort_key, reverse=True)[:50]:
        lines.append(f"- **{it['lang'].upper()}** [{it['title']}]({it['url']}) "
                     f"· {it['outlet']} · {', '.join(it['categories'])}")
    text = "\n".join(lines) + "\n"
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backfill", action="store_true",
                        help="search as far back as each source allows")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG_PATH.read_text("utf-8"))
    store = load_store()
    backfill = args.backfill or not store
    days = None if backfill else int(cfg.get("lookback_days", 7))
    run_time = now_utc()
    log(f"WAICO tracker run at {iso(run_time)} "
        f"({'backfill' if backfill else f'last {days} days'}, {len(store)} items in archive)")

    searches = cfg.get("searches", {})
    by_source: dict[str, int] = {}
    raw: list[dict] = []
    for name, func in (("google_news", google_news), ("bing_news", bing_news), ("gdelt", gdelt)):
        if not searches.get(name):
            continue
        found = func(searches[name], days)
        by_source[name] = len(found)
        raw.extend(found)

    if FETCH_STATS["ok"] == 0:
        # Every request failed: stop with an error so GitHub flags the run,
        # and leave the archive untouched.
        log("Every source failed to respond. Nothing was changed.")
        return 1

    new_items = merge(store, raw, cfg, run_time)
    categorize_all(store, cfg)
    stats = {"last_run_new": len(new_items), "by_source": by_source, "total": len(store)}
    write_outputs(store, cfg, run_time, stats)
    write_job_summary(new_items, stats)
    log(f"Done: {len(new_items)} new, {len(store)} total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
