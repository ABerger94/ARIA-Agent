"""Top-headlines feed for the Command Center's News tab (Port 7).

The Mark-LVI News tab is a pushed reading surface; ARIA-AGENT's
dashboard had no equivalent, so this tab pairs the surface with an
honest pull source: Google News' public top-stories RSS. No API key,
no new dependency (urllib + ElementTree), and every failure returns
an empty list — the tab says it could not load rather than inventing
headlines. Fetching is blocking; callers run it on a worker thread.
"""
from __future__ import annotations

import html
import urllib.request
import xml.etree.ElementTree as ET

_FEED_URL = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
_UA = "ARIA-Agent dashboard news tab"


def fetch_headlines(limit: int = 12) -> list:
    """[{title, source, link, published}] newest-first, [] on failure."""
    try:
        req = urllib.request.Request(_FEED_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
        root = ET.fromstring(payload)
    except Exception:
        return []
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        items.append({
            "title": title,
            "source": source,
            "link": link,
            "published": (node.findtext("pubDate") or "").strip(),
        })
        if len(items) >= limit:
            break
    return items


def format_headlines_html(items) -> str:
    """Render headlines as link-rich HTML for a QTextBrowser."""
    if not items:
        return ("<p style='color:#829aaf'>Could not load headlines — "
                "check the network and press Refresh.</p>")
    out = []
    for it in items:
        title = html.escape(it["title"])
        link = html.escape(it["link"], quote=True)
        meta = " · ".join(x for x in (html.escape(it["source"]),
                                      html.escape(it["published"])) if x)
        out.append(
            f"<p style='margin:0 0 2px 0'>"
            f"<a style='color:#77e7ef' href='{link}'>{title}</a></p>"
            + (f"<p style='margin:0 0 10px 0;color:#829aaf'>{meta}</p>"
               if meta else "<p style='margin:0 0 10px 0'></p>"))
    return "".join(out)
