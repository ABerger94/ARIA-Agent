"""Small gap tools (aria/tools/gaptools.py) — 2026-10-27.

Pins the Agent-native versions of the Mark-LVI flight_finder,
game_updater, browser_control, reminder, update_status, dev_agent
and code_helper actions. Every external seam is faked at the
module's own helper (_builtins / _provider_text / _run_python /
_launch_uri / _run / _os_name / _workspace_dir), so nothing here
touches the network, a real browser, Steam, or the OS scheduler.
aria.outcome is absent in this tree while it is built in parallel,
so these also pin the plain-string fallback line shape.
"""
import importlib
import os
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

gap = importlib.import_module("aria.tools.gaptools")

TODAY = date(2026, 10, 27)  # a Tuesday


# ── flight_finder: parse_date ───────────────────────────────────────


def test_parse_date_iso_and_slash_formats():
    assert gap.parse_date("2026-11-05", TODAY) == "2026-11-05"
    assert gap.parse_date("11/05/2026", TODAY) == "2026-11-05"
    # Ambiguity resolves MM/DD (US user): 05/11 is May 11, not 5 Nov.
    assert gap.parse_date("05/11/2026", TODAY) == "2026-05-11"
    # MM/DD impossible (month 13) -> the DD/MM reading is used.
    assert gap.parse_date("13/05/2026", TODAY) == "2026-05-13"


def test_parse_date_relative_words():
    assert gap.parse_date("today", TODAY) == "2026-10-27"
    assert gap.parse_date("tomorrow", TODAY) == "2026-10-28"
    assert gap.parse_date("Tomorrow", TODAY) == "2026-10-28"


def test_parse_date_month_name_year_rule():
    # Month later this year -> this year.
    assert gap.parse_date("November 5", TODAY) == "2026-11-05"
    assert gap.parse_date("December 25", TODAY) == "2026-12-25"
    # Current month -> this year (the rule keys on the month).
    assert gap.parse_date("October 3", TODAY) == "2026-10-03"
    # Month already past this year -> next year.
    assert gap.parse_date("March 3", TODAY) == "2027-03-03"
    assert gap.parse_date("Jan 15", TODAY) == "2027-01-15"
    # 'D Month' order and an explicit year both work.
    assert gap.parse_date("5 November", TODAY) == "2026-11-05"
    assert gap.parse_date("March 3, 2028", TODAY) == "2028-03-03"


def test_parse_date_garbage_is_none():
    assert gap.parse_date("", TODAY) is None
    assert gap.parse_date(None, TODAY) is None
    assert gap.parse_date("next friday-ish", TODAY) is None
    assert gap.parse_date("2026-13-40", TODAY) is None
    assert gap.parse_date("Smarch 3", TODAY) is None


# ── flight_finder: URL builder + handler ────────────────────────────


def test_build_flights_url_one_way_and_cabins():
    url = gap.build_flights_url("BWI", "LHR", "2026-11-05")
    q = parse_qs(urlparse(url).query)
    assert q["q"] == ["Flights from BWI to LHR on 2026-11-05"]
    assert q["cabin"] == ["1"] and q["adults"] == ["1"]
    assert q["curr"] == ["USD"]
    for cabin, code in (("economy", "1"), ("premium", "2"),
                        ("business", "3"), ("first", "4")):
        u = gap.build_flights_url("BWI", "LHR", "2026-11-05",
                                   cabin=cabin)
        assert parse_qs(urlparse(u).query)["cabin"] == [code]


def test_build_flights_url_round_trip_and_passengers():
    url = gap.build_flights_url("New York", "Los Angeles",
                                "2026-11-05", "2026-11-12",
                                passengers=3, cabin="business")
    q = parse_qs(urlparse(url).query)
    assert q["q"] == ["Flights from New York to Los Angeles on "
                      "2026-11-05 returning 2026-11-12"]
    assert q["adults"] == ["3"] and q["cabin"] == ["3"]


class _FakeBuiltins:
    def __init__(self):
        self.opened = []
        self.writes = {}
        self.fetch_text = "page text here"
        self.fetch_error = None

    def tool_open_app_or_url(self, target):
        self.opened.append(target)
        return f"Opened URL: {target}"

    def tool_fetch_url(self, url):
        if self.fetch_error:
            raise self.fetch_error
        return self.fetch_text

    def tool_write_file(self, filename, content):
        self.writes[filename] = content
        return f"Successfully wrote {len(content)} characters to {filename}."


@pytest.fixture
def fake_builtins(monkeypatch):
    fake = _FakeBuiltins()
    monkeypatch.setattr(gap, "_builtins", lambda: fake)
    return fake


def test_flight_finder_opens_url_unverified(fake_builtins):
    out = gap.tool_flight_finder({
        "origin": "BWI", "destination": "LHR", "date": "2026-11-05",
        "return_date": "11/12/2026", "cabin": "business",
        "passengers": 2})
    assert out.startswith("status=unverified")
    assert "flight_finder" in out
    assert len(fake_builtins.opened) == 1
    url = fake_builtins.opened[0]
    assert url in out and "2026-11-12" in out
    assert "NOT scraped" in out  # prices are never invented


def test_flight_finder_missing_fields_fail(fake_builtins):
    assert gap.tool_flight_finder(
        {"destination": "LHR", "date": "2026-11-05"}
    ).startswith("status=failed")
    assert gap.tool_flight_finder(
        {"origin": "BWI", "destination": "LHR"}
    ).startswith("status=failed")
    assert gap.tool_flight_finder(
        {"origin": "BWI", "destination": "LHR", "date": "whenever"}
    ).startswith("status=failed")
    assert fake_builtins.opened == []


def test_flight_finder_open_failure_still_returns_url(monkeypatch):
    class Boom:
        def tool_open_app_or_url(self, target):
            raise RuntimeError("no browser")
    monkeypatch.setattr(gap, "_builtins", lambda: Boom())
    out = gap.tool_flight_finder({"origin": "BWI", "destination": "LHR",
                                  "date": "tomorrow"})
    assert out.startswith("status=unverified")
    assert "google.com/travel/flights" in out


# ── game_updater: manifests + steam tree ────────────────────────────

ACF_A = '''"AppState"
{
	"appid"		"111"
	"Universe"		"1"
	"name"		"Alpha Game"
	"StateFlags"		"4"
	"installdir"		"AlphaGame"
	"SizeOnDisk"		"2048"
	"UpdateResult"		"0"
}
'''
ACF_B = '''"AppState"
{
	"appid"		"222"
	"name"		"Beta Game"
	"StateFlags"		"6"
	"UpdateResult"		"3"
}
'''


def test_parse_appmanifest():
    data = gap.parse_appmanifest(ACF_A)
    assert data["appid"] == 111 and data["name"] == "Alpha Game"
    assert data["StateFlags"] == 4 and data["UpdateResult"] == 0
    assert data["installdir"] == "AlphaGame"


def _steam_tree(tmp_path):
    steam = tmp_path / "Steam"
    apps = steam / "steamapps"
    apps.mkdir(parents=True)
    (apps / "appmanifest_111.acf").write_text(ACF_A)
    lib2 = tmp_path / "SteamLibrary"
    (lib2 / "steamapps").mkdir(parents=True)
    (lib2 / "steamapps" / "appmanifest_222.acf").write_text(ACF_B)
    (apps / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n'
        '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
        % (steam.as_posix(), lib2.as_posix()))
    return steam


def test_steam_games_two_libraries(tmp_path):
    steam = _steam_tree(tmp_path)
    assert gap.find_steam_path(steam) == steam
    assert gap.find_steam_path(tmp_path / "nope") is None
    games = gap.steam_games(steam)
    assert [g["name"] for g in games] == ["Alpha Game", "Beta Game"]
    assert games[0]["appid"] == 111 and games[1]["appid"] == 222


def test_game_updater_list_status_update(tmp_path, monkeypatch):
    steam = _steam_tree(tmp_path)
    launched = []
    monkeypatch.setattr(gap, "_launch_uri", launched.append)

    out = gap.tool_game_updater({"action": "list",
                                 "steam_path": str(steam)})
    assert out.startswith("status=done") and "games=2" in out
    assert "Alpha Game" in out and "Beta Game" in out

    # Beta has StateFlags 0x2 set + UpdateResult 3 -> flagged.
    out = gap.tool_game_updater({"action": "status",
                                 "steam_path": str(steam)})
    assert "pending=1" in out and "Beta Game" in out

    out = gap.tool_game_updater({"action": "update", "game": "beta",
                                 "steam_path": str(steam)})
    assert out.startswith("status=unverified")
    assert launched == ["steam://validate/222"]

    launched.clear()
    out = gap.tool_game_updater({"action": "update",
                                 "steam_path": str(steam)})
    assert launched == ["steam://validate/222"]  # only flagged games

    out = gap.tool_game_updater({"action": "update", "game": "zzz",
                                 "steam_path": str(steam)})
    assert out.startswith("status=failed")


def test_game_updater_no_steam_is_honest_failure(tmp_path):
    out = gap.tool_game_updater({"action": "list",
                                 "steam_path": str(tmp_path / "none")})
    assert out.startswith("status=failed")
    assert "Steam was not found" in out and "Epic" in out
    assert gap.tool_game_updater(
        {"action": "dance"}).startswith("status=failed")


# ── browser_control ─────────────────────────────────────────────────


def test_browser_go_to_and_get_text(fake_builtins, monkeypatch):
    # 2026-10-28: go_to no longer calls the builtins launcher directly;
    # it routes through the dispatcher as open_app_or_url. Pin the new
    # seam: the dispatch helper is what receives the URL.
    def _launch(url):
        fake_builtins.opened.append(url)
        return f"Opened URL: {url}", False
    monkeypatch.setattr(gap, "_open_url_via_dispatch", _launch)
    out = gap.tool_browser_control({"action": "go_to",
                                    "url": "https://example.com"})
    assert out.startswith("status=unverified")
    assert fake_builtins.opened == ["https://example.com"]

    fake_builtins.fetch_text = "x" * 5000
    out = gap.tool_browser_control({"action": "get_text",
                                    "url": "https://example.com"})
    assert out.startswith("status=done") and "chars=4000" in out
    assert out.split("\n", 1)[1] == "x" * 4000  # truncated at 4000


def test_browser_failures(fake_builtins):
    assert gap.tool_browser_control(
        {"action": "peek", "url": "https://x"}
    ).startswith("status=failed")
    assert gap.tool_browser_control(
        {"action": "go_to"}).startswith("status=failed")
    fake_builtins.fetch_text = "[Could not fetch https://x]"
    assert gap.tool_browser_control(
        {"action": "get_text", "url": "https://x"}
    ).startswith("status=failed")
    fake_builtins.fetch_error = RuntimeError("boom")
    assert gap.tool_browser_control(
        {"action": "get_text", "url": "https://x"}
    ).startswith("status=failed")


# ── os_reminder ─────────────────────────────────────────────────────


def test_sanitise_strips_quotes_controls_and_caps():
    dirty = 'He said "don\'t" \\ \nnext\tline\x01!'
    clean = gap.sanitise(dirty)
    assert '"' not in clean and "'" not in clean
    assert "\\" not in clean and "\n" not in clean and "\x01" not in clean
    assert len(gap.sanitise("y" * 500)) == 200
    assert len(gap.sanitise("y" * 500, 10)) == 10


def test_task_name_for():
    when = datetime(2026, 10, 27, 15, 30)
    name = gap.task_name_for("Take your meds!", when)
    assert name.startswith("ARIA Reminder 202610271530 ")
    assert "take-your-meds" in name
    assert gap.task_name_for("!!!", when).endswith("reminder")


def test_build_notify_script_per_os():
    win = gap.build_notify_script("Take meds", "win32")
    assert "System.Windows.Forms" in win and "MessageBox" in win
    assert "Take meds" in win
    linux = gap.build_notify_script("Take meds", "linux")
    assert "notify-send" in linux and "zenity" in linux
    mac = gap.build_notify_script("Take meds", "darwin")
    assert "osascript" in mac and "display dialog" in mac


def test_os_reminder_past_time_refused_on_any_platform():
    past = (datetime.now() - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M")
    out = gap.tool_os_reminder({"message": "too late", "at": past})
    assert out.startswith("status=failed")
    assert "has passed" in out
    out = gap.tool_os_reminder({"message": "no when at all"})
    assert out.startswith("status=failed")


def test_os_reminder_win32_schtasks_seam(tmp_path, monkeypatch):
    monkeypatch.setattr(gap, "_os_name", lambda: "win32")
    monkeypatch.setattr(gap, "_workspace_dir", lambda: tmp_path)
    calls = []

    def ok_run(cmd):
        calls.append(cmd)
        return 0, "SUCCESS"
    monkeypatch.setattr(gap, "_run", ok_run)

    future = (datetime.now() + timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M")
    out = gap.tool_os_reminder({"message": "Stretch break",
                                "at": future})
    assert out.startswith("status=done")
    assert calls and calls[0][0] == "schtasks"
    assert "/Create" in calls[0] and "/SC" in calls[0]
    tn = calls[0][calls[0].index("/TN") + 1]
    assert tn.startswith("ARIA Reminder")
    scripts = list((tmp_path / "os_reminders").glob("*.ps1"))
    assert len(scripts) == 1
    assert "Stretch break" in scripts[0].read_text()

    def bad_run(cmd):
        return 1, "ERROR: access denied"
    monkeypatch.setattr(gap, "_run", bad_run)
    out = gap.tool_os_reminder({"message": "Stretch break",
                                "at": future})
    assert out.startswith("status=failed")
    assert "access denied" in out


def test_os_reminder_delay_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(gap, "_os_name", lambda: "win32")
    monkeypatch.setattr(gap, "_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(gap, "_run", lambda cmd: (0, ""))
    out = gap.tool_os_reminder({"message": "Soon", "delay_minutes": 30})
    assert out.startswith("status=done")


# ── update_status ───────────────────────────────────────────────────


def test_render_status_with_and_without_head():
    snap = {"inflight": [{"tool": "x"}], "recent": [{}, {}],
            "delegated": [{"active": True}, {"active": False},
                          {"active": True}]}
    out = gap.render_status("abcdef1234567890", snap,
                            datetime(2026, 10, 27, 8, 0))
    assert out.startswith("status=done")
    assert "head=abcdef12" in out
    assert "inflight=1" in out and "recent=2" in out
    assert "delegated_active=2" in out
    out = gap.render_status(None, None, datetime(2026, 10, 27, 8, 0))
    assert out.startswith("status=unverified")
    assert "inflight=0" in out


def test_git_head_on_this_repo():
    if not (Path(PKG).parent / ".git").exists():
        pytest.skip("not a git checkout")
    import shutil as _sh
    if not _sh.which("git"):
        pytest.skip("git not installed")
    head = gap.git_head(Path(PKG).parent)
    assert head and len(head) >= 7
    assert gap.git_head("/nonexistent/path/xyz") is None


# ── code_helper / dev_agent ─────────────────────────────────────────


@pytest.fixture
def fake_provider(monkeypatch):
    calls = []

    def provider(system, prompt, role="code"):
        calls.append((system, prompt, role))
        if "Explain" in prompt:
            return "It adds two numbers."
        if "Find the bug" in prompt:
            return "def add(a, b):\n    return a + b"
        return "```python\nprint('generated')\n```"
    monkeypatch.setattr(gap, "_provider_text", provider)
    return calls


def test_code_helper_explain_and_fix(fake_provider, fake_builtins):
    out = gap.tool_code_helper({"action": "explain",
                                "code": "def add(a,b): return a+b"})
    assert out.startswith("status=done")
    assert "It adds two numbers." in out
    out = gap.tool_code_helper({"action": "fix", "code": "broken(",
                                "instruction": "syntax"})
    assert "return a + b" in out
    assert gap.tool_code_helper(
        {"action": "explain"}).startswith("status=failed")
    assert gap.tool_code_helper(
        {"action": "dance", "code": "x"}).startswith("status=failed")


def test_code_helper_reads_file_and_caps(tmp_path, fake_provider):
    f = tmp_path / "big.py"
    f.write_text("z" * 30000)
    out = gap.tool_code_helper({"action": "explain",
                                "file_path": str(f)})
    assert out.startswith("status=done")
    prompt = fake_provider[-1][1]
    assert prompt.count("z") == 20000  # the read cap
    out = gap.tool_code_helper({"action": "explain",
                                "file_path": str(tmp_path / "no.py")})
    assert out.startswith("status=failed")


def test_code_helper_write_saves_via_builtins(fake_provider,
                                             fake_builtins):
    out = gap.tool_code_helper({"action": "write",
                                "description": "print generated",
                                "output_path": "gen.py"})
    assert out.startswith("status=done") and "path=gen.py" in out
    # Markdown fences from the provider are stripped before saving.
    assert fake_builtins.writes["gen.py"] == "print('generated')"
    assert gap.tool_code_helper(
        {"action": "write", "description": "x"}
    ).startswith("status=failed")  # output_path is required


def test_code_helper_provider_failure(monkeypatch, fake_builtins):
    def boom(system, prompt, role="code"):
        raise RuntimeError("provider down")
    monkeypatch.setattr(gap, "_provider_text", boom)
    out = gap.tool_code_helper({"action": "explain", "code": "x = 1"})
    assert out.startswith("status=failed")
    assert "provider down" in out


def test_dev_agent_saves_and_never_runs(monkeypatch, fake_builtins):
    # Security, 2026-10-27: dev_agent saves but NEVER executes. Its
    # old run path executed generated code while the dispatcher
    # checked policy only against the outer name 'dev_agent' — in
    # neither SAFE_MODE_BLOCKED_TOOLS nor DESTRUCTIVE_TOOLS — so
    # run=true bypassed safe mode and approval outright.
    monkeypatch.setattr(gap, "_provider_text",
                        lambda s, p, role="code":
                        "```python\nprint('hi from dev')\n```")
    ran = []
    monkeypatch.setattr(gap, "_run_python",
                        lambda code: ran.append(code) or "hi from dev")

    out = gap.tool_dev_agent({"description": "say hi",
                              "output_path": "hi.py"})
    assert out.startswith("status=done") and "path=hi.py" in out
    assert fake_builtins.writes["hi.py"] == "print('hi from dev')"
    assert ran == []  # not run unless asked

    out = gap.tool_dev_agent({"description": "say hi",
                              "output_path": "hi.py", "run": True})
    assert out.startswith("status=failed")  # saved, NOT executed
    assert fake_builtins.writes["hi.py"] == "print('hi from dev')"
    assert ran == []  # execution must never happen from here
    assert "NOT executed" in out
    assert "run_python_code" in out  # the gated route for running it

    out = gap.tool_dev_agent({"description": "say hi",
                              "language": "rust",
                              "output_path": "hi.rs", "run": True})
    assert out.startswith("status=failed")
    assert "NOT executed" in out
    assert ran == []
    assert gap.tool_dev_agent({}).startswith("status=failed")
