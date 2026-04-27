#!/usr/bin/env python3
"""Weekly IT News Collector — generates a static HTML digest from Google News RSS feeds."""

import feedparser
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
import hashlib
from email.utils import parsedate_to_datetime

TOPICS = [
    {"id": "windows",    "name": "Microsoft Windows",    "query": "Microsoft Windows",                       "color": "#0078D4"},
    {"id": "azure",      "name": "Azure",                "query": "Microsoft Azure cloud",                   "color": "#00BCF2"},
    {"id": "sharepoint", "name": "SharePoint",           "query": "Microsoft SharePoint",                    "color": "#036C70"},
    {"id": "intune",     "name": "Intune",               "query": "Microsoft Intune",                        "color": "#0F6CBD"},
    {"id": "entraid",    "name": "Entra ID",             "query": '"Entra ID" Microsoft',                    "color": "#6B69D6"},
    {"id": "m365",       "name": "Microsoft 365",        "query": '"Microsoft 365" OR "Office 365"',         "color": "#D83B01"},
    {"id": "iru",        "name": "Iru MDM (formerly Kandji)", "query": "Kandji OR \"Iru MDM\" Apple device management Mac", "color": "#7C3AED"},
    {"id": "cato",       "name": "Cato Networks",        "query": "Cato Networks",                           "color": "#FF6B35"},
]

GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
MAX_PER_TOPIC = 8
HISTORY_WEEKS = 26
OUT_DIR = Path(".")


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
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def fetch_articles(topic: dict, since: datetime) -> list:
    url = GNEWS.format(q=quote_plus(topic["query"]))
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"  [!] Error fetching {topic['name']}: {e}", file=sys.stderr)
        return []

    results = []
    for entry in feed.entries[:30]:
        dt = parse_pub_date(entry)
        if dt < since:
            continue
        link = entry.get("link", "")
        summary = clean_html(entry.get("summary", ""))
        if len(summary) > 350:
            summary = summary[:347] + "…"

        source = ""
        src = entry.get("source")
        if src:
            source = src.get("title", "") if isinstance(src, dict) else getattr(src, "title", "")

        results.append({
            "id": article_id(link),
            "title": clean_html(entry.get("title", "No title")),
            "link": link,
            "source": source,
            "date": dt.strftime("%b %d, %Y"),
            "summary": summary,
        })
        if len(results) >= MAX_PER_TOPIC:
            break

    return results


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
    return f"""      <div class="article-card" data-id="{a['id']}">
        <div class="article-body">
          <a href="{a['link']}" target="_blank" rel="noopener noreferrer" class="article-title">{esc(a['title'])}</a>
          <div class="article-meta">{esc(a['source'])} &bull; {a['date']}</div>
          <p class="article-summary">{esc(a['summary'])}</p>
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
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}}
    a{{color:var(--accent)}}

    /* Header */
    header{{background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
    .h-inner{{max-width:1000px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}}
    .h-brand{{font-size:1.1rem;font-weight:800;color:#1d4ed8}}
    .h-meta{{font-size:.78rem;color:var(--muted)}}
    .h-controls{{display:flex;gap:8px;align-items:center}}
    .btn{{padding:5px 12px;border-radius:6px;font-size:.78rem;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--text);white-space:nowrap}}
    .btn:hover{{background:#f1f5f9}}
    select.week-sel{{padding:5px 10px;border-radius:6px;border:1px solid var(--border);background:var(--surface);font-size:.78rem;cursor:pointer;color:var(--text)}}

    /* Topic nav */
    .t-nav{{background:var(--surface);border-bottom:1px solid var(--border);overflow-x:auto}}
    .t-nav-inner{{max-width:1000px;margin:0 auto;padding:8px 20px;display:flex;gap:6px;min-width:max-content}}
    .nav-link{{font-size:.72rem;padding:3px 10px;border-radius:20px;border:2px solid var(--c,#2563eb);color:var(--c,#2563eb);text-decoration:none;font-weight:600;white-space:nowrap}}
    .nav-link:hover{{background:var(--c,#2563eb);color:#fff}}

    /* Main */
    main{{max-width:1000px;margin:0 auto;padding:24px 20px}}

    .week-hero{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px 24px;margin-bottom:28px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}}
    .hero-title{{font-size:1.6rem;font-weight:900;color:#1d4ed8}}
    .hero-sub{{font-size:.85rem;color:var(--muted);margin-top:2px}}
    .stat-box{{text-align:center;min-width:60px}}
    .stat-num{{font-size:2rem;font-weight:900;color:#1d4ed8;line-height:1}}
    .stat-lbl{{font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}

    /* Topic */
    .topic-section{{margin-bottom:40px}}
    .topic-title{{font-size:1.2rem;font-weight:800;color:var(--c,#2563eb);border-bottom:3px solid var(--c,#2563eb);padding-bottom:8px;margin-bottom:14px}}
    .count{{font-size:.8rem;font-weight:400;color:var(--muted)}}
    .no-news{{color:var(--muted);font-style:italic;font-size:.88rem}}

    /* Article card */
    .article-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:10px;display:flex;gap:12px;align-items:flex-start;transition:border-color .15s}}
    .article-card:hover{{border-color:#94a3b8}}
    .article-body{{flex:1;min-width:0}}
    .article-title{{font-size:.95rem;font-weight:600;text-decoration:none;display:block;margin-bottom:3px;color:var(--accent)}}
    .article-title:hover{{text-decoration:underline}}
    .article-meta{{font-size:.72rem;color:var(--muted);margin-bottom:5px}}
    .article-summary{{font-size:.85rem;color:#475569}}

    /* Hide button */
    .hide-btn{{flex-shrink:0;padding:5px 10px;border-radius:6px;font-size:.72rem;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--muted);white-space:nowrap;align-self:flex-start}}
    .hide-btn:hover{{border-color:#ef4444;color:#ef4444;background:#fef2f2}}
    .lbl-show{{display:none}}

    /* Hidden state */
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

function saveHidden(s) {{
  localStorage.setItem(KEY, JSON.stringify([...s]));
}}

function toggleArticle(id) {{
  const h = getHidden();
  h.has(id) ? h.delete(id) : h.add(id);
  saveHidden(h);
  applyHidden(h);
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

// Load week archive navigation
fetch('weeks.json').then(r => r.json()).then(weeks => {{
  const sel = document.getElementById('week-sel');
  weeks.forEach(w => {{
    const o = document.createElement('option');
    o.value = w.file;
    o.textContent = w.label;
    if (w.current) o.selected = true;
    sel.appendChild(o);
  }});
}}).catch(() => {{}});

applyHidden();
</script>
</body>
</html>"""


def main():
    now = datetime.now(timezone.utc)
    week_label = iso_week_label(now)
    date_range = week_date_range(now)
    since = now - timedelta(days=7)

    print(f"Collecting IT news for {week_label} ({date_range})")
    print(f"Articles from: {since.strftime('%Y-%m-%d %H:%M UTC')}\n")

    topics_data = []
    for topic in TOPICS:
        print(f"  Fetching: {topic['name']}...", end=" ", flush=True)
        articles = fetch_articles(topic, since)
        print(f"{len(articles)} articles")
        topics_data.append({"topic": topic, "articles": articles})

    print()
    page_html = render_page(week_label, date_range, topics_data)

    # Write week archive file
    week_file = OUT_DIR / f"week-{week_label}.html"
    week_file.write_text(page_html, encoding="utf-8")
    print(f"Written:  {week_file}")

    # Update index.html (always = current week)
    index_file = OUT_DIR / "index.html"
    index_file.write_text(page_html, encoding="utf-8")
    print(f"Updated:  index.html")

    # Update weeks.json
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
    # Clear "current" flag on old entries, remove duplicate for this week
    existing = [dict(e, current=False) for e in existing if e.get("week") != week_label]
    existing.insert(0, current_entry)
    existing = existing[:HISTORY_WEEKS]

    # Remove old week files beyond history window
    kept = {e["file"] for e in existing}
    for old in OUT_DIR.glob("week-*.html"):
        if old.name not in kept:
            old.unlink()
            print(f"Pruned:   {old.name}")

    weeks_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated:  weeks.json ({len(existing)} weeks in archive)")
    print(f"\nDone! Total articles: {sum(len(t['articles']) for t in topics_data)}")


if __name__ == "__main__":
    main()
