"""Pass 28 #18: queue crash recovery must run per project, not
only for the project active at singleton init.

Before: get_queue() ran taskstore.recover_interrupted() once,
when the singleton was first created, under whatever project was
current then. The journal is project-scoped (workspaces.path),
so a project first used AFTER the singleton was built never had
its interrupted records recovered — they sat as 'running' /
'pending' forever, invisible to resumable().

Fix contract pinned here: get_queue() recovers the CURRENT
project the first time it is seen in this process (once per
project), so switching to a project recovers its journal too.
"""
import importlib
import os
import sys
import tempfile
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskqueue_recovery_"), "test.db")
_memory.init_databases()
_memory._embed = lambda text: None

for _name in ("aria.scheduler", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.hud"):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules["aria.vision"].get_face_frame_jpeg = lambda: None
sys.modules["aria.vision"].publish_phone_frame = lambda jpeg: True
sys.modules["aria.vision"].get_phone_frame_jpeg = lambda: None
sys.modules["aria.vision"].get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
sys.modules["aria.vision"].describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"

# taskstore writes under config.WORKSPACE_DIR: scratch dir.
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskqueue-recovery-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")
workspaces = importlib.import_module("aria.workspaces")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def test_recovery_runs_for_project_first_seen_after_singleton_init():
    saved_queue = taskqueue._queue
    saved_recovered = set(getattr(taskqueue, "_recovered_projects", set()))
    taskqueue._queue = None
    taskqueue._recovered_projects = set()
    proj = workspaces.create("Project B")
    try:
        # An interrupted record in Project B's journal, planted
        # while the default project is current.
        with workspaces.use(proj):
            taskstore.create("p1", "project work")  # status running
            assert taskstore.get("p1")["status"] == "running"
        # The singleton is created under the default project:
        # only the default journal is recovered.
        q = taskqueue.get_queue()
        with workspaces.use(proj):
            assert taskstore.get("p1")["status"] == "running", \
                "recovery ran for a project that was never active"
        # Switching to Project B and touching the queue recovers
        # its journal too.
        with workspaces.use(proj):
            q2 = taskqueue.get_queue()
            assert q2 is q
            assert taskstore.get("p1")["status"] == "blocked", \
                "Project B's interrupted record was never recovered"
            assert "interrupted" in (taskstore.get("p1")["blocked_reason"] or "")
        # A second touch does not re-recover (once per project).
        with workspaces.use(proj):
            taskqueue.get_queue()
            assert taskstore.get("p1")["status"] == "blocked"
    finally:
        q = taskqueue._queue
        taskqueue._queue = saved_queue
        taskqueue._recovered_projects = saved_recovered
        if q is not None:
            q.stop()
