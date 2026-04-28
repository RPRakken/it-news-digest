#!/usr/bin/env python3
"""Weekly IT News Collector — generates a static HTML digest from multiple RSS sources."""

import feedparser
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
import hashlib
from email.utils import parsedate_to_datetime
import trafilatura
import requests

_REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_SESSION = requests.Session()
_SESSION.headers.update(_REQ_HEADERS)

# ---------------------------------------------------------------------------
# Topic definitions — each topic lists direct RSS feeds (with optional keyword
# filter) and a Google News query as fallback.
# ---------------------------------------------------------------------------
TOPICS = [
    {
        "id": "windows", "name": "Microsoft Windows", "color": "#0078D4",
        "feeds": [
            {"url": "https://blogs.windows.com/feed/"},
            {"url": "https://petri.com/feed/", "kw": "windows"},
            {"url": "https://www.bleepingcomputer.com/feed/", "kw": "windows"},
        ],
        "gnews": "Microsoft Windows",
    },
    {
        "id": "azure", "name": "Azure", "color": "#00BCF2",
        "feeds": [
            {"url": "https://azure.microsoft.com/en-us/blog/feed/"},
            {"url": "https://petri.com/feed/", "kw": "azure"},
        ],
        "gnews": "Microsoft Azure cloud",
    },
    {
        "id": "sharepoint", "name": "SharePoint", "color": "#036C70",
        "feeds": [
            {"url": "https://petri.com/feed/", "kw": "sharepoint"},
            {"url": "https://www.bleepingcomputer.com/feed/", "kw": "sharepoint"},
            {"url": "https://www.theregister.com/security/headlines.atom", "kw": "sharepoint"},
            {"url": "https://feeds.feedburner.com/TheHackersNews", "kw": "sharepoint"},
        ],
        "gnews": "Microsoft SharePoint",
    },
    {
        "id": "intune", "name": "Intune", "color": "#0F6CBD",
        "feeds": [
            {"url": "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=IntuneCustomerSuccess"},
            {"url": "https://petri.com/feed/", "kw": "intune"},
        ],
        "gnews": "Microsoft Intune",
    },
    {
        "id": "entraid", "name": "Entra ID", "color": "#6B69D6",
        "feeds": [
            {"url": "https://petri.com/feed/", "kw": "entra"},
            {"url": "https://www.bleepingcomputer.com/feed/", "kw": "entra"},
            {"url": "https://www.theregister.com/security/headlines.atom", "kw": "entra"},
            {"url": "https://feeds.feedburner.com/TheHackersNews", "kw": "entra"},
        ],
        "gnews": '"Entra ID" Microsoft',
    },
    {
        "id": "m365", "name": "Microsoft 365", "color": "#D83B01",
        "feeds": [
            {"url": "https://www.microsoft.com/en-us/microsoft-365/blog/feed/"},
            {"url": "https://petri.com/feed/", "kw": "microsoft 365"},
            {"url": "https://www.bleepingcomputer.com/feed/", "kw": "office 365"},
        ],
        "gnews": '"Microsoft 365" OR "Office 365"',
    },
    {
        "id": "iru", "name": "Iru MDM (formerly Kandji)", "color": "#7C3AED",
        "feeds": [
            {"url": "https://9to5mac.com/feed/", "kw": "kandji"},
            {"url": "https://9to5mac.com/feed/", "kw": "iru mdm"},
            {"url": "https://petri.com/feed/", "kw": "kandji"},
        ],
        "gnews": 'Kandji OR "Iru MDM" Apple Mac device management',
    },
    {
        "id": "cato", "name": "Cato Networks", "color": "#FF6B35",
        "feeds": [
            {"url": "https://www.securityweek.com/feed/", "kw": "cato networks"},
            {"url": "https://www.theregister.com/security/headlines.atom", "kw": "cato"},
            {"url": "https://feeds.feedburner.com/TheHackersNews", "kw": "cato"},
        ],
        "gnews": "Cato Networks",
    },
]

GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
MAX_PER_TOPIC = 8
HISTORY_WEEKS = 26
ARTICLE_MAX_CHARS = 4000
OUT_DIR = Path(".")

# Extra feeds used only for title→URL resolution (not for topic discovery)
BRIDGE_FEEDS = [
    "https://www.bleepingcomputer.com/feed/",
    "https://www.securityweek.com/feed/",
    "https://petri.com/feed/",
    "https://9to5mac.com/feed/",
    "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=IntuneCustomerSuccess",
    "https://o365reports.com/feed/",
    "https://practical365.com/feed/",
    "https://www.thelazyadministrator.com/feed/",
    "https://www.theregister.com/security/headlines.atom",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://www.neowin.net/news/rss/",
]

# Cache fetched feeds to avoid repeated HTTP calls for the same URL
_feed_cache: dict = {}
# title (lowercase, stripped) → real article URL
_title_index: dict = {}


def _slug(title: str) -> str:
    """Normalise a title for fuzzy matching.
    Google News appends ' - Source Name' to titles; strip it before comparing.
    """
    t = clean_html(title).strip()
    # Remove trailing ' - Source Name' or ' | Source Name' suffix
    t = re.sub(r"\s*[-|]\s*[A-Z][^|–-]{2,40}$", "", t).strip()
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def build_title_index() -> None:
    """Pre-cache bridge feeds and build a title→URL lookup table."""
    for url in BRIDGE_FEEDS:
        feed = get_feed(url)
        for entry in feed.entries:
            link = entry.get("link", "")
            title = _slug(entry.get("title", ""))
            if link and title and "news.google.com" not in link:
                _title_index[title] = link


def resolve_url(gnews_title: str) -> str:
    """Try to find the real article URL for a Google News title."""
    needle = _slug(gnews_title)
    if needle in _title_index:
        return _title_index[needle]
    # Fuzzy: check if any known title is a substring (≥70% word overlap)
    needle_words = set(needle.split())
    if len(needle_words) >= 4:
        for known_title, known_url in _title_index.items():
            known_words = set(known_title.split())
            overlap = len(needle_words & known_words) / len(needle_words)
            if overlap >= 0.7:
                return known_url
    return ""


def article_id(url: str) -> str:
    return "a" + hashlib.md5(url.encode()).hexdigest()[:10]


def parse_pub_date(entry) -> datetime:
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def esc(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def get_feed(url: str) -> object:
    """Fetch and cache an RSS feed."""
    if url not in _feed_cache:
        try:
            _feed_cache[url] = feedparser.parse(url)
        except Exception as e:
            print(f"    [!] Feed error {url[:60]}: {e}", file=sys.stderr)
            _feed_cache[url] = feedparser.FeedParserDict(entries=[])
    return _feed_cache[url]


def fetch_article_text(url: str) -> str:
    """Fetch full article text via trafilatura; returns '' on failure."""
    if "news.google.com" in url:
        return ""
    try:
        # trafilatura.fetch_url handles timeouts, redirects and retries better than requests
        downloaded = trafilatura.fetch_url(
            url,
            config=trafilatura.settings.use_config()
        )
        if not downloaded:
            # Fallback: try requests with longer timeout
            try:
                resp = _SESSION.get(url, timeout=20, allow_redirects=True)
                downloaded = resp.text if resp.status_code == 200 else None
            except Exception:
                return ""
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        ) or ""
        text = text.strip()
        if len(text) > ARTICLE_MAX_CHARS:
            cut = text[:ARTICLE_MAX_CHARS].rfind(". ")
            text = text[:cut + 1] if cut > ARTICLE_MAX_CHARS // 2 else text[:ARTICLE_MAX_CHARS] + "…"
        return text
    except Exception:
        return ""


def entry_matches_keyword(entry, kw: str) -> bool:
    """True if the keyword appears in the title or summary."""
    kw_lower = kw.lower()
    title = clean_html(entry.get("title", "")).lower()
    summary = clean_html(entry.get("summary", "")).lower()
    return kw_lower in title or kw_lower in summary


def fetch_topic_articles(topic: dict, since: datetime) -> list:
    seen_urls: set = set()
    results: list = []

    # ---- Direct RSS feeds ----
    for feed_cfg in topic.get("feeds", []):
        feed_url = feed_cfg["url"]
        kw = feed_cfg.get("kw")
        feed = get_feed(feed_url)

        for entry in feed.entries:
            if len(results) >= MAX_PER_TOPIC:
                break
            dt = parse_pub_date(entry)
            if dt < since:
                continue
            if kw and not entry_matches_keyword(entry, kw):
                continue

            link = entry.get("link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            source = ""
            src = entry.get("source")
            if src:
                source = (src.get("title", "") if isinstance(src, dict)
                          else getattr(src, "title", ""))

            # Try to get full article text from the real URL
            full_text = fetch_article_text(link)
            summary = full_text if full_text else clean_html(entry.get("summary", ""))

            results.append({
                "id": article_id(link),
                "title": clean_html(entry.get("title", "No title")),
                "link": link,
                "source": source or _domain(link),
                "date": dt.strftime("%b %d, %Y"),
                "summary": summary,
                "has_full_text": bool(full_text),
            })

        if len(results) >= MAX_PER_TOPIC:
            break

    # ---- Google News fallback ----
    if len(results) < MAX_PER_TOPIC and topic.get("gnews"):
        q = quote_plus(topic["gnews"])
        feed = get_feed(GNEWS.format(q=q))
        for entry in feed.entries:
            if len(results) >= MAX_PER_TOPIC:
                break
            dt = parse_pub_date(entry)
            if dt < since:
                continue
            link = entry.get("link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            source = ""
            src = entry.get("source")
            if src:
                source = (src.get("title", "") if isinstance(src, dict)
                          else getattr(src, "title", ""))

            title_raw = clean_html(entry.get("title", "No title"))
            # Try to resolve Google News redirect to a real URL via title matching
            real_url = resolve_url(title_raw)
            full_text = fetch_article_text(real_url) if real_url else ""
            summary = full_text if full_text else clean_html(entry.get("summary", ""))

            results.append({
                "id": article_id(link),
                "title": title_raw,
                "link": real_url or link,
                "source": source,
                "date": dt.strftime("%b %d, %Y"),
                "summary": summary,
                "has_full_text": bool(full_text),
            })

    return results


def _domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def iso_week_label(dt: datetime) -> str:
    cal = dt.isocalendar()
    return f"{cal[0]}-W{cal[1]:02d}"


def week_date_range(dt: datetime) -> str:
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.strftime('%b %d')}–{sunday.strftime('%d, %Y')}"
    elif monday.year == sunday.year:
        return f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"
    return f"{monday.strftime('%b %d, %Y')} – {sunday.strftime('%b %d, %Y')}"


def render_article(a: dict) -> str:
    if a["has_full_text"]:
        paragraphs = [p.strip() for p in a["summary"].split("\n\n") if p.strip()]
        inner = ("".join(f"<p>{esc(p)}</p>" for p in paragraphs)
                 if len(paragraphs) > 1 else f"<p>{esc(a['summary'])}</p>")
        summary_html = f'<div class="article-summary">{inner}</div>'
    elif a["summary"]:
        # Short RSS snippet — show it but prompt the reader to click through
        summary_html = (
            f'<div class="article-summary snippet">'
            f'<p>{esc(a["summary"])}</p>'
            f'<a href="{a["link"]}" target="_blank" rel="noopener noreferrer" class="read-more">'
            f'Read full article &rarr;</a>'
            f'</div>'
        )
    else:
        summary_html = (
            f'<div class="article-summary snippet">'
            f'<a href="{a["link"]}" target="_blank" rel="noopener noreferrer" class="read-more">'
            f'Read full article &rarr;</a>'
            f'</div>'
        )

    return f"""      <div class="article-card" data-id="{a['id']}">
        <div class="article-body">
          <a href="{a['link']}" target="_blank" rel="noopener noreferrer" class="article-title">{esc(a['title'])}</a>
          <div class="article-meta">{esc(a['source'])} &bull; {a['date']}</div>
          {summary_html}
        </div>
        <button class="hide-btn" onclick="toggleArticle('{a['id']}')" title="Collapse for colleagues">
          <span class="lbl-hide">&#128065; Hide</span>
          <span class="lbl-show">&#128064; Show</span>
        </button>
      </div>"""


def render_topic(topic: dict, articles: list) -> str:
    count_label = f'<span class="count">({len(articles)})</span>' if articles else ""
    body = "\n".join(render_article(a) for a in articles) if articles else \
        '      <p class="no-news">No articles found this week.</p>'
    return f"""    <section id="{topic['id']}" class="topic-section">
      <h2 class="topic-title" style="--c:{topic['color']}">{esc(topic['name'])} {count_label}</h2>
{body}
    </section>"""


def render_page(week_label: str, date_range: str, topics_data: list) -> str:
    total = sum(len(t["articles"]) for t in topics_data)
    topics_html = "\n".join(render_topic(t["topic"], t["articles"]) for t in topics_data)
    nav_links = "".join(
        f'<a href="#{t["topic"]["id"]}" class="nav-link" style="--c:{t["topic"]["color"]}">'
        f'{esc(t["topic"]["name"])}</a>'
        for t in topics_data
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IT News Digest — {week_label}</title>
  <style>
    :root{{--bg:#f8fafc;--surface:#fff;--border:#e2e8f0;--text:#1e293b;--muted:#64748b;--accent:#2563eb}}
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.7}}
    a{{color:var(--accent)}}

    header{{background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
    .h-inner{{max-width:1000px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}}
    .h-brand{{font-size:1.1rem;font-weight:800;color:#1d4ed8}}
    .h-meta{{font-size:.78rem;color:var(--muted)}}
    .h-controls{{display:flex;gap:8px;align-items:center}}
    .btn{{padding:5px 12px;border-radius:6px;font-size:.78rem;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--text);white-space:nowrap}}
    .btn:hover{{background:#f1f5f9}}
    select.week-sel{{padding:5px 10px;border-radius:6px;border:1px solid var(--border);background:var(--surface);font-size:.78rem;cursor:pointer;color:var(--text)}}

    .t-nav{{background:var(--surface);border-bottom:1px solid var(--border);overflow-x:auto}}
    .t-nav-inner{{max-width:1000px;margin:0 auto;padding:8px 20px;display:flex;gap:6px;min-width:max-content}}
    .nav-link{{font-size:.72rem;padding:3px 10px;border-radius:20px;border:2px solid var(--c,#2563eb);color:var(--c,#2563eb);text-decoration:none;font-weight:600;white-space:nowrap}}
    .nav-link:hover{{background:var(--c,#2563eb);color:#fff}}

    main{{max-width:1000px;margin:0 auto;padding:24px 20px}}

    .week-hero{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px 24px;margin-bottom:28px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}}
    .hero-title{{font-size:1.6rem;font-weight:900;color:#1d4ed8}}
    .hero-sub{{font-size:.85rem;color:var(--muted);margin-top:2px}}
    .stat-box{{text-align:center;min-width:60px}}
    .stat-num{{font-size:2rem;font-weight:900;color:#1d4ed8;line-height:1}}
    .stat-lbl{{font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}

    .topic-section{{margin-bottom:40px}}
    .topic-title{{font-size:1.2rem;font-weight:800;color:var(--c,#2563eb);border-bottom:3px solid var(--c,#2563eb);padding-bottom:8px;margin-bottom:14px}}
    .count{{font-size:.8rem;font-weight:400;color:var(--muted)}}
    .no-news{{color:var(--muted);font-style:italic;font-size:.88rem}}

    .article-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px;display:flex;gap:12px;align-items:flex-start;transition:border-color .15s}}
    .article-card:hover{{border-color:#94a3b8}}
    .article-body{{flex:1;min-width:0}}
    .article-title{{font-size:.98rem;font-weight:600;text-decoration:none;display:block;margin-bottom:4px;color:var(--accent)}}
    .article-title:hover{{text-decoration:underline}}
    .article-meta{{font-size:.72rem;color:var(--muted);margin-bottom:8px}}

    .article-summary{{margin-top:6px;font-size:.875rem;color:#334155;line-height:1.75}}
    .article-summary p{{margin-bottom:.6em}}
    .article-summary p:last-child{{margin-bottom:0}}
    .gnews-badge{{display:inline-block;font-size:.65rem;background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 5px;margin-bottom:6px;vertical-align:middle}}
    .article-summary.snippet{{color:var(--muted)}}
    .read-more{{display:inline-block;margin-top:6px;font-size:.8rem;font-weight:600;color:var(--accent);text-decoration:none}}
    .read-more:hover{{text-decoration:underline}}

    .hide-btn{{flex-shrink:0;padding:5px 10px;border-radius:6px;font-size:.72rem;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--muted);white-space:nowrap;align-self:flex-start}}
    .hide-btn:hover{{border-color:#ef4444;color:#ef4444;background:#fef2f2}}
    .lbl-show{{display:none}}

    .article-card.is-hidden{{display:none}}
    body.reveal-hidden .article-card.is-hidden{{display:flex;opacity:.45;border-style:dashed}}
    .article-card.is-hidden .lbl-hide{{display:none}}
    .article-card.is-hidden .lbl-show{{display:inline}}
    body.reveal-hidden .article-card.is-hidden .hide-btn{{border-color:#16a34a;color:#16a34a}}
    body.reveal-hidden .article-card.is-hidden .hide-btn:hover{{background:#f0fdf4}}

    footer{{text-align:center;padding:24px 20px;color:var(--muted);font-size:.75rem;border-top:1px solid var(--border);margin-top:40px}}

    @media(max-width:600px){{
      .h-inner,.main{{padding-left:14px;padding-right:14px}}
      .hero-title{{font-size:1.3rem}}
    }}
  </style>
</head>
<body>

<header>
  <div class="h-inner">
    <div>
      <div class="h-brand">IT News Digest</div>
      <div class="h-meta">Week {week_label} &bull; {date_range}</div>
    </div>
    <div class="h-controls">
      <button class="btn" id="reveal-btn" onclick="toggleReveal()">Show hidden</button>
      <select class="week-sel" id="week-sel" onchange="if(this.value) location.href=this.value">
        <option value="">&#128337; Archive&hellip;</option>
      </select>
    </div>
  </div>
</header>

<nav class="t-nav"><div class="t-nav-inner">{nav_links}</div></nav>

<main>
  <div class="week-hero">
    <div>
      <div class="hero-title">Week {week_label}</div>
      <div class="hero-sub">{date_range}</div>
    </div>
    <div class="stat-box">
      <div class="stat-num">{total}</div>
      <div class="stat-lbl">Articles</div>
    </div>
  </div>

{topics_html}
</main>

<footer>
  Generated every Sunday &mdash; Topics: Windows &bull; Azure &bull; SharePoint &bull; Intune &bull; Entra ID &bull; Microsoft 365 &bull; Iru MDM (formerly Kandji) &bull; Cato Networks
</footer>

<script>
const KEY = 'it-news-hidden-v1';
function getHidden() {{
  try {{ return new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); }}
  catch {{ return new Set(); }}
}}
function saveHidden(s) {{ localStorage.setItem(KEY, JSON.stringify([...s])); }}
function toggleArticle(id) {{
  const h = getHidden();
  h.has(id) ? h.delete(id) : h.add(id);
  saveHidden(h); applyHidden(h);
}}
function applyHidden(h) {{
  h = h || getHidden();
  document.querySelectorAll('.article-card[data-id]').forEach(c => {{
    c.classList.toggle('is-hidden', h.has(c.dataset.id));
  }});
}}
let revealing = false;
function toggleReveal() {{
  revealing = !revealing;
  document.body.classList.toggle('reveal-hidden', revealing);
  document.getElementById('reveal-btn').textContent = revealing ? 'Hide hidden' : 'Show hidden';
}}
fetch('weeks.json').then(r => r.json()).then(weeks => {{
  const sel = document.getElementById('week-sel');
  weeks.forEach(w => {{
    const o = document.createElement('option');
    o.value = w.file; o.textContent = w.label;
    if (w.current) o.selected = true;
    sel.appendChild(o);
  }});
}}).catch(() => {{}});
applyHidden();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    week_label = iso_week_label(now)
    date_range = week_date_range(now)
    since = now - timedelta(days=7)

    print(f"Collecting IT news for {week_label} ({date_range})")
    print(f"Articles from: {since.strftime('%Y-%m-%d %H:%M UTC')}\n")

    print("  Building title index from bridge feeds...", flush=True)
    build_title_index()
    print(f"  Index: {len(_title_index)} known articles\n")

    topics_data = []
    for topic in TOPICS:
        print(f"  {topic['name']}...", end=" ", flush=True)
        articles = fetch_topic_articles(topic, since)
        full = sum(1 for a in articles if a.get("has_full_text"))
        print(f"{len(articles)} articles ({full} with full text)")
        topics_data.append({"topic": topic, "articles": articles})

    print()
    page_html = render_page(week_label, date_range, topics_data)

    week_file = OUT_DIR / f"week-{week_label}.html"
    week_file.write_text(page_html, encoding="utf-8")
    print(f"Written:  {week_file}")

    index_file = OUT_DIR / "index.html"
    index_file.write_text(page_html, encoding="utf-8")
    print(f"Updated:  index.html")

    weeks_file = OUT_DIR / "weeks.json"
    existing: list = []
    if weeks_file.exists():
        try:
            existing = json.loads(weeks_file.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    cal = now.isocalendar()
    current_entry = {
        "week": week_label,
        "file": f"week-{week_label}.html",
        "label": f"Week {cal[1]}, {cal[0]} — {date_range}",
        "current": True,
    }
    existing = [dict(e, current=False) for e in existing if e.get("week") != week_label]
    existing.insert(0, current_entry)
    existing = existing[:HISTORY_WEEKS]

    kept = {e["file"] for e in existing}
    for old in OUT_DIR.glob("week-*.html"):
        if old.name not in kept:
            old.unlink()
            print(f"Pruned:   {old.name}")

    weeks_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    total_articles = sum(len(t["articles"]) for t in topics_data)
    total_full = sum(a.get("has_full_text", False) for t in topics_data for a in t["articles"])
    print(f"Updated:  weeks.json ({len(existing)} weeks)")
    print(f"\nDone! {total_articles} articles, {total_full} with full text (~50 lines)")


if __name__ == "__main__":
    main()
