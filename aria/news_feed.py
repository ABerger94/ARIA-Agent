"""Top-headlines feed for the Command Center's News tab (Port 7).

The Mark-LVI News tab is a pushed reading surface; ARIA-AGENT's
dashboard had no equivalent, so this tab pairs the surface with an
honest pull source: Google News' public top-stories RSS. No API key,
no new dependency (urllib + ElementTree), and every failure returns
an empty list — the tab says it could not load rather than inventing
headlines. Fetching is blocking; callers run it on a worker thread.

Sections (Alek, 2026-10-27 — the briefing and the tab now drink
from the same well): beside Top Stories, the tab groups National /
World / Tech from Google News' section feeds, and aria/briefing.py
builds its National/World/Tech sections from these same feeds — so
what the greeting says is what the tab shows.
"""
from __future__ import annotations

import html
import threading
import urllib.request
import xml.etree.ElementTree as ET

_FEED_URL = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
_SECTION_URL = ("https://news.google.com/rss/headlines/section/topic/"
                "{topic}?hl=en-US&gl=US&ceid=US:en")
_UA = "ARIA-Agent dashboard news tab"
SECTIONS = (("National", "NATION"), ("World", "WORLD"),
            ("Tech", "TECHNOLOGY"))


def _fetch_url(url: str, limit: int) -> list:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
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


def fetch_headlines(limit: int = 12) -> list:
    """[{title, source, link, published}] newest-first, [] on failure."""
    return _fetch_url(_FEED_URL, limit)


def fetch_section(topic: str, limit: int = 12) -> list:
    """One Google News section feed (NATION / WORLD / TECHNOLOGY /
    ...), same item shape and failure honesty as fetch_headlines."""
    return _fetch_url(_SECTION_URL.format(topic=topic), limit)


def fetch_grouped(limit: int = 12) -> list:
    """[(label, items)] — Top Stories plus every section, fetched
    concurrently (each feed keeps its own failure: [] and the group
    renders its empty line, never a borrowed headline)."""
    jobs = [("Top stories", None)] + list(SECTIONS)
    grouped = [None] * len(jobs)

    def run(i, topic):
        grouped[i] = fetch_headlines(limit) if topic is None \
            else fetch_section(topic, limit)

    threads = [threading.Thread(target=run, args=(i, topic), daemon=True)
               for i, (_label, topic) in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)
    return [(label, grouped[i] or [])
            for i, (label, _topic) in enumerate(jobs)]


def _items_html(items) -> str:
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


def format_headlines_html(items) -> str:
    """Render headlines as link-rich HTML for a QTextBrowser."""
    if not items:
        return ("<p style='color:#829aaf'>Could not load headlines — "
                "check the network and press Refresh.</p>")
    return _items_html(items)


def format_grouped_html(grouped) -> str:
    """The tab's grouped rendering: a header per section, its
    headlines under it. All groups empty is the one 'Could not
    load' state; a single empty group says so in one quiet line."""
    if not any(items for _label, items in grouped):
        return ("<p style='color:#829aaf'>Could not load headlines — "
                "check the network and press Refresh.</p>")
    out = []
    for label, items in grouped:
        out.append(
            f"<p style='margin:14px 0 6px 0;color:#54d9e8;"
            f"font-size:15px'><b>{html.escape(label.upper())}</b></p>")
        out.append(_items_html(items) if items else
                   "<p style='margin:0 0 10px 0;color:#829aaf'>"
                   "No headlines right now.</p>")
    return "".join(out)
