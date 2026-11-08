"""Pass 29 #33: mirrored backups carry the project registry and
workflow state, with a versioned recovery manifest.

The gap: backup_slots() returned only the memory DB,
settings.json, and chat_history.md — no workspaces.json,
delegated_tasks.json, routines, triggers, or reminder_gate.
A restore from that mirror lost every named project, every
routine, and the delegated-task journal, even though those
files are load-bearing (workspaces.py raises without a valid
registry; routines live in routines/*.json).

The fix: backup_slots() also returns those stores, and every
mirror write lands a recovery_manifest.json alongside them —
versioned, allowlisted, names/kinds only (never contents, so
no secret can leak through it), with restore declared
metadata-only: nothing restored executes automatically.
"""
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_pass29_backup_")
os.environ["ARIA_DATA_ROOT"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules.setdefault("aria", _pkg)

import importlib

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
workspaces = importlib.import_module("aria.workspaces")
data_dir = importlib.import_module("aria.data_dir")

from aria import backup_mirror as bm


def _stub_settings(monkeypatch, store=None):
    store = {} if store is None else store
    monkeypatch.setattr(
        bm, "_get_setting",
        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(
        bm, "_set_setting",
        lambda key, value: store.__setitem__(key, value))
    return store


def _layout(tmp_path):
    """A fake install root: registry naming project 'alpha',
    one routine, a delegated-task journal, triggers + gate."""
    root = tmp_path / "root"
    (root / "routines").mkdir(parents=True)
    (root / "workspaces.json").write_text(json.dumps({
        "active": "alpha",
        "projects": {
            "default": {"id": "default", "name": "Personal",
                        "settings": {}},
            "alpha": {"id": "alpha", "name": "Alpha Project",
                      "settings": {}}}}))
    (root / "routines" / "morning.json").write_text(json.dumps({
        "name": "morning", "steps": [
            {"tool": "save_memory", "args": {"key": "x"}}]}))
    (root / "routines" / "evening.json").write_text(json.dumps({
        "name": "evening", "steps": []}))
    (root / "delegated_tasks.json").write_text(json.dumps({
        "task-1": {"goal": "do the thing", "status": "blocked"}}))
    (root / "triggers.json").write_text(json.dumps(
        [{"id": 1, "name": "mail-watch"}]))
    (root / "triggers_state.json").write_text(json.dumps({}))
    (root / "reminder_gate.json").write_text(json.dumps(
        {"known": True}))
    (root / "settings.json").write_text(json.dumps(
        {"voice_enabled": True}))
    (root / "chat_history.md").write_text("# chat\n")
    (root / "aria_keys.json").write_text(json.dumps(
        {"GMAIL_APP_PASSWORD": "sekrit-live-value"}))
    return root


def _point_resolvers(monkeypatch, root):
    """Point every resolver backup_slots() uses at the fake root."""
    monkeypatch.setattr(memory, "db_path",
                        lambda: str(root / "aria_memory.db"))
    monkeypatch.setattr(config, "SETTINGS_FILE",
                        str(root / "settings.json"))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(root))
    monkeypatch.setattr(
        workspaces, "path",
        lambda name, legacy=None: str(root / name))
    monkeypatch.setattr(data_dir, "path",
                        lambda name: str(root / name))
    monkeypatch.setattr(data_dir, "subdir",
                        lambda name: str(root / name))


def test_backup_slots_include_registry_routines_and_workflow(
        monkeypatch, tmp_path):
    root = _layout(tmp_path)
    _point_resolvers(monkeypatch, root)
    names = [p.name for p in bm.backup_slots()]
    assert names[:3] == ["aria_memory.db", "settings.json",
                         "chat_history.md"]
    for required in ("workspaces.json", "delegated_tasks.json",
                     "triggers.json", "triggers_state.json",
                     "reminder_gate.json", "morning.json",
                     "evening.json"):
        assert required in names, names
    assert not any("aria_keys" in str(p) for p in bm.backup_slots())


def test_mirror_writes_manifest_and_all_recovery_files(
        monkeypatch, tmp_path):
    root = _layout(tmp_path)
    _point_resolvers(monkeypatch, root)
    mirror = tmp_path / "mirror"
    _stub_settings(monkeypatch, {"backup_mirror_dir": str(mirror)})
    # The fake memory DB is not SQLite; mirror it as a plain file
    # by pointing the DB slot at a real SQLite file instead.
    import sqlite3
    from contextlib import closing
    db = root / "aria_memory.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE facts(value TEXT)")
        conn.execute("INSERT INTO facts VALUES('x')")
        conn.commit()
    assert bm.snapshot_and_mirror() is True
    # The registry's active project is 'alpha', so the mirror
    # lands under projects/alpha (mirror_backups' project scope).
    dest = mirror / "projects" / "alpha"
    for name in ("workspaces.json", "delegated_tasks.json",
                 "triggers.json", "triggers_state.json",
                 "reminder_gate.json", "morning.json", "evening.json",
                 "settings.json", "chat_history.md"):
        assert (dest / name).is_file(), name
    manifest = json.loads(
        (dest / "recovery_manifest.json").read_text())
    assert manifest["version"] == bm.RECOVERY_MANIFEST_VERSION == 1
    kinds = {e["name"]: e["kind"] for e in manifest["entries"]}
    assert kinds["workspaces.json"] == "project_registry"
    assert kinds["delegated_tasks.json"] == "delegated_tasks"
    assert kinds["morning.json"] == "routine"
    assert kinds["triggers.json"] == "triggers"
    assert kinds["reminder_gate.json"] == "reminder_gate"
    assert manifest["excluded"] == ["aria_keys.json"]
    assert "aria_keys.json" not in [
        e["name"] for e in manifest["entries"]]
    # Secrets never mirror: the live key value appears nowhere
    # in the mirror, manifest included.
    assert "sekrit-live-value" not in (
        dest / "recovery_manifest.json").read_text()
    assert not (dest / "aria_keys.json").exists()
    # The manifest says restore is metadata only.
    assert "Never automatically execute" in manifest["restore_policy"]


def test_backup_taken_and_restored_recognizes_projects_and_routines(
        monkeypatch, tmp_path):
    root = _layout(tmp_path)
    _point_resolvers(monkeypatch, root)
    mirror = tmp_path / "mirror"
    _stub_settings(monkeypatch, {"backup_mirror_dir": str(mirror)})
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(root / "aria_memory.db")) as conn:
        conn.execute("CREATE TABLE facts(value TEXT)")
        conn.commit()
    assert bm.snapshot_and_mirror() is True

    # Restore: copy the mirrored files back into a fresh root,
    # then read them the way their owners do.
    mirror = mirror / "projects" / "alpha"
    fresh = tmp_path / "fresh"
    (fresh / "routines").mkdir(parents=True)
    for name in ("workspaces.json", "delegated_tasks.json",
                 "morning.json", "evening.json"):
        src = mirror / name
        dest = (fresh / "routines" / name if name.endswith(".json")
                and name in ("morning.json", "evening.json")
                else fresh / name)
        dest.write_bytes(src.read_bytes())
    registry = json.loads((fresh / "workspaces.json").read_text())
    assert registry["projects"]["alpha"]["name"] == "Alpha Project"
    assert registry["active"] == "alpha"
    routine = json.loads(
        (fresh / "routines" / "morning.json").read_text())
    assert routine["name"] == "morning"
    assert routine["steps"][0]["tool"] == "save_memory"
    journal = json.loads(
        (fresh / "delegated_tasks.json").read_text())
    assert journal["task-1"]["status"] == "blocked"


def test_manifest_marks_required_entries(monkeypatch, tmp_path):
    root = _layout(tmp_path)
    _point_resolvers(monkeypatch, root)
    manifest = bm.recovery_manifest()
    by_name = {e["name"]: e for e in manifest["entries"]}
    assert by_name["workspaces.json"]["required"] is True
    assert by_name["delegated_tasks.json"]["required"] is True
    assert by_name["morning.json"]["required"] is False
    for name in ("workspaces.json", "delegated_tasks.json",
                 "morning.json", "settings.json", "chat_history.md"):
        assert by_name[name]["present"] is True, name
    # A missing source is listed as absent, not silently dropped.
    (root / "triggers.json").unlink()
    manifest = bm.recovery_manifest()
    by_name = {e["name"]: e for e in manifest["entries"]}
    assert by_name["triggers.json"]["present"] is False
