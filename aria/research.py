"""ARIA research briefs (Alek, approved 2026-10-28).

Deeper than `web_search`: the `research` tool searches, READS the
top sources in full, synthesizes a cited brief through the provider
chain, then runs an adversarial cross-check pass over its own brief
before filing it. Briefs live in the current project's research/
as Markdown with an index (research_index.json) — the default
project keeps the legacy WORKSPACE_DIR/research/ location, so
existing briefs stay where they were; the dashboard's Briefing tab
appends a Research section listing the newest ones, and the tool can
list and re-read them.

Citations are the point: a brief she cannot show her work for is a
rumor. (The spoken greeting stays headlines-only per Alek's rule —
citations live in the filed brief, not in speech.)

All network/provider seams are injectable for tests: search, fetch,
synth, check.
"""

import json
import os
import re
import threading
from datetime import datetime

from aria.config import WORKSPACE_DIR, add_log

RESEARCH_DIR = os.path.join(WORKSPACE_DIR, "research")
_INDEX_NAME = "research_index.json"

# Project-scoped index lock (pass 29 #43): the brief id is allocated
# from the project's own index, so the load → file → append → save
# sequence must be atomic per project.
_INDEX_LOCK = threading.Lock()

_MAX_SOURCES = 4
_SOURCE_CHARS = 6000

_SYNTH_SYSTEM = (
    "You are ARIA's research analyst. Write a brief from the numbered "
    "sources ONLY. Format: first line 'SHORT ANSWER: <one or two "
    "sentences>'. Then a line 'FINDINGS:' followed by bullet points; "
    "every factual bullet ends with the citation(s) it rests on, like "
    "[1] or [2][3]. Then a line 'UNCERTAINTIES:' with bullets for "
    "anything the sources disagree on or do not cover. Never invent "
    "facts, numbers, dates or quotes that are not in the sources. If "
    "the sources are thin, say so plainly."
)

_CHECK_SYSTEM = (
    "You are a skeptical fact-checker. You are given a research brief "
    "and the numbered sources it cites. List every claim in the brief "
    "that is NOT supported by the cited source text, every citation "
    "pointing at a source that does not contain its claim, and any "
    "important counter-evidence in the sources the brief ignored. If "
    "everything checks out, reply exactly: 'No unsupported claims "
    "found.' Be terse."
)


# ---------------- storage ----------------

def _research_dir() -> str:
    # Pass 29 #43: briefs and the index are per-project. The
    # default project's path is the legacy global dir (RESEARCH_DIR)
    # passed as the legacy fallback, so existing global briefs stay
    # with the default project — history is neither moved nor
    # duplicated. A named project gets its own research/ under its
    # project root.
    from aria import workspaces
    directory = workspaces.path("research", RESEARCH_DIR)
    os.makedirs(directory, exist_ok=True)
    return directory


def _index_path() -> str:
    return os.path.join(_research_dir(), _INDEX_NAME)


class ResearchIndexCorrupt(Exception):
    """The research index exists but is not a valid JSON list.

    Pass 36 #37: a corrupt index is NOT an empty index — the old
    _load_index returned [] for any read/parse error, so the next
    run restarted ids at 1 and orphaned every older brief from
    the index. Callers must surface this, never silently empty.
    """


def _preserve_corrupt_index(path: str, exc: Exception) -> str:
    """Keep a timestamped copy of a corrupt index next to it, so
    the records are recoverable by hand. Best effort, never raises."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{path}.corrupt-{stamp}"
    try:
        import shutil
        shutil.copyfile(path, dest)
        return dest
    except Exception:
        return ""


def _load_index() -> list:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:
        dest = _preserve_corrupt_index(_index_path(), exc)
        raise ResearchIndexCorrupt(
            "research index is corrupt"
            + (f" (preserved as {dest})" if dest else "")
            + f": {exc}") from exc
    if not isinstance(data, list):
        dest = _preserve_corrupt_index(_index_path(), TypeError(
            "index is not a JSON list"))
        raise ResearchIndexCorrupt(
            "research index is corrupt: not a JSON list"
            + (f" (preserved as {dest})" if dest else ""))
    return data


def _save_index(entries: list) -> None:
    # Pass 36 #37: atomic write — temp file in the same directory
    # + os.replace, so an interrupted save can never truncate the
    # live index in place (the old open("w") did exactly that).
    import tempfile
    path = _index_path()
    fd, tmp = tempfile.mkstemp(prefix=".research_index.",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _slug(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    return slug[:48] or "brief"


# ---------------- seams (real implementations) ----------------

def _ddgs_search(query: str, max_results: int = 6) -> list:
    """DuckDuckGo text search returning title/href/body dicts."""
    try:
        from duckduckgo_search import DDGS
    except Exception:
        return []
    try:
        results = DDGS().text(query, max_results=max_results)
    except Exception as exc:
        add_log(f"research: search failed: {exc}")
        return []
    out = []
    for r in results or []:
        href = (r.get("href") or "").strip()
        if href:
            out.append({"title": (r.get("title") or href).strip(),
                        "href": href,
                        "body": (r.get("body") or "").strip()})
    return out


def _fetch_text(url: str) -> str:
    """Fetch a page and strip it to readable text (builtins' machinery)."""
    from aria.tools import builtins
    html = builtins._fetch_html(url)
    if not html:
        return ""
    return builtins._html_to_text(html)


def _provider(system: str, user: str) -> str:
    from aria.agent.providers import provider_text
    return provider_text(system, user, role="default")


# ---------------- the run ----------------

def run_research(question: str, *, search=None, fetch=None,
                 synth=None, check=None) -> dict:
    """Run one research pass and file the brief.

    Returns {"ok": True, "id", "question", "summary", "file",
    "sources", "verification"} or {"ok": False, "error"}.
    "verification" is "verified" when the cross-check ran and
    "unverified" when the checker was unavailable (the brief is
    still filed — an unverified draft is useful, but it must never
    be reported as cross-checked).
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "research needs a question."}
    search = search or _ddgs_search
    fetch = fetch or _fetch_text
    synth = synth or _provider
    check = check or _provider

    add_log(f"research: starting on '{question[:60]}'")
    hits = search(question)
    if not hits:
        return {"ok": False,
                "error": "No sources found — search returned nothing."}

    sources = []
    for hit in hits[:_MAX_SOURCES]:
        text = fetch(hit["href"])
        if text:
            sources.append({"title": hit["title"], "url": hit["href"],
                            "text": text[:_SOURCE_CHARS]})
    if not sources:
        return {"ok": False,
                "error": "Found pages but none could be read."}

    numbered = "\n\n".join(
        f"[{i + 1}] {s['title']}\n{s['url']}\n{s['text']}"
        for i, s in enumerate(sources))
    brief = synth(_SYNTH_SYSTEM,
                  f"Question: {question}\n\nSources:\n{numbered}")
    if not brief or brief.startswith("["):
        return {"ok": False,
                "error": f"Synthesis failed: {brief or 'empty response'}"}

    verdict = check(_CHECK_SYSTEM,
                    f"Question: {question}\n\nBrief:\n{brief}\n\n"
                    f"Sources:\n{numbered}")
    # Pass 29 #47: a missing/bracketed checker reply means the
    # cross-check never ran — record that as unverified, never as
    # a successful cross-check. The brief is still filed (an
    # unverified draft is useful), but everything downstream must
    # say so.
    verified = True
    if not verdict or verdict.startswith("["):
        verdict = "(cross-check unavailable — treat the brief as unverified)"
        verified = False
    verification = "verified" if verified else "unverified"

    now = datetime.now()
    summary = ""
    for line in brief.splitlines():
        if line.strip().upper().startswith("SHORT ANSWER:"):
            summary = line.split(":", 1)[1].strip()
            break
    if not summary:
        summary = next((ln.strip() for ln in brief.splitlines()
                        if ln.strip()), "")[:300]

    with _INDEX_LOCK:
        try:
            entries = _load_index()
        except ResearchIndexCorrupt as exc:
            # Pass 36 #37: a corrupt index blocks filing — writing
            # the brief first would orphan it (no index entry) and
            # the next run would restart ids at 1. Surface it.
            return {"ok": False, "error": str(exc)}
        rid = (entries[-1]["id"] + 1) if entries else 1
        fname = f"{_slug(question)}-{now.strftime('%Y%m%d-%H%M%S')}.md"
        doc = (
            f"# Research brief: {question}\n"
            f"Filed {now.strftime('%Y-%m-%d %H:%M')} · {len(sources)} sources "
            f"· brief #{rid} · {verification}\n\n"
            f"{brief.strip()}\n\n"
            f"## Cross-check\n{verdict.strip()}\n\n"
            f"## Sources\n"
            + "\n".join(f"{i + 1}. {s['title']} — {s['url']}"
                        for i, s in enumerate(sources))
            + "\n"
        )
        with open(os.path.join(_research_dir(), fname), "w",
                  encoding="utf-8") as f:
            f.write(doc)
        entries.append({"id": rid, "question": question,
                        "filed_at": now.isoformat(timespec="seconds"),
                        "file": fname, "summary": summary,
                        "verification": verification,
                        "sources": [s["url"] for s in sources]})
        _save_index(entries)
    add_log(f"research: brief #{rid} filed ({fname})")
    return {"ok": True, "id": rid, "question": question,
            "summary": summary, "file": fname,
            "verification": verification,
            "sources": len(sources),
            # Pass 36 #36: durable artifact receipt for the filed
            # brief — a repair/replay gate can check this instead
            # of re-running run and filing a second brief.
            "receipt": {"brief_id": rid, "file": fname,
                        "filed_at": now.isoformat(timespec="seconds")}}


def list_research(limit: int = 5) -> list:
    """Newest briefs first: [{id, question, filed_at, file, summary}].

    Raises ResearchIndexCorrupt when the index is corrupt — the
    caller surfaces it; an empty list means "no briefs", never
    "the index could not be read" (pass 36 #37).
    """
    return list(reversed(_load_index()))[:limit]


def read_research(rid) -> str:
    """The full text of one filed brief, by id."""
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return f"No research brief with id '{rid}'."
    try:
        entries = _load_index()
    except ResearchIndexCorrupt as exc:
        return f"Cannot read research briefs: {exc}"
    for entry in entries:
        if entry["id"] == rid:
            path = os.path.join(_research_dir(), entry["file"])
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return f"Brief #{rid} is indexed but its file is missing."
    return f"No research brief with id '{rid}'."


def research_section_text(limit: int = 5) -> str:
    """Dashboard-ready Research section for the Briefing tab."""
    try:
        entries = list_research(limit)
    except ResearchIndexCorrupt as exc:
        return f"\n\n—— RESEARCH BRIEFS ——\n({exc})"
    if not entries:
        return ""
    lines = ["", "", "—— RESEARCH BRIEFS ——"]
    for e in entries:
        when = str(e.get("filed_at", ""))[:10]
        lines.append(f"• #{e['id']} [{when}] {e['question']}")
        if e.get("summary"):
            lines.append(f"  {e['summary']}")
    lines.append("(Ask me to read a brief aloud, or open it from the "
                 "research folder.)")
    return "\n".join(lines)


# ---------------- the tool ----------------

def tool_research(args) -> str:
    args = args or {}
    action = str(args.get("action") or "run").strip().lower()
    if action == "list":
        try:
            entries = list_research(8)
        except ResearchIndexCorrupt as exc:
            return f"Cannot list research briefs: {exc}"
        if not entries:
            return "No research briefs filed yet."
        return "Research briefs (newest first):\n" + "\n".join(
            f"#{e['id']} [{str(e['filed_at'])[:10]}] {e['question']} — "
            f"{e['summary']}"
            + (" (unverified)" if e.get("verification") == "unverified"
               else "")
            for e in entries)
    if action == "read":
        return read_research(args.get("id"))
    if action != "run":
        return (f"research: unknown action '{action}' — "
                "use run, list, or read.")
    result = run_research(str(args.get("question") or ""))
    if not result["ok"]:
        # Pass 28 #31: "Research failed: ..." normalized as DONE.
        from aria.outcome import failed
        return failed("research", result["error"])
    # Pass 29 #47: never report "cross-checked" when the checker
    # was unavailable — the status comes from run_research.
    if result.get("verification") == "unverified":
        status = (f"Based on {result['sources']} sources — unverified: "
                  "the cross-check was unavailable, so treat this "
                  "brief as unverified. ")
    else:
        status = f"Based on {result['sources']} sources, cross-checked. "
    return (f"Research brief #{result['id']} filed: {result['question']}\n"
            f"Short answer: {result['summary']}\n"
            f"{status}"
            f"Full brief is in the Briefing tab's Research section "
            f"({result['file']}). "
            f"Receipt: brief #{result['id']} ({result['file']}) — "
            f"already filed; do not re-run to file it again.")
