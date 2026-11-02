"""Named project storage. The default project keeps the existing install's data.

Executors bind a project with use(); changing the desktop selection never moves
an in-flight task to another project's files. Application credentials and
approval mode remain installation-wide.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
from pathlib import Path, PureWindowsPath
import re
import threading
import uuid

_BOUND = contextvars.ContextVar('aria_workspace', default=None)
_LOCK = threading.RLock()
_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')


def _manifest():
    from aria import data_dir
    return Path(data_dir.path('workspaces.json'))


def _read():
    path = _manifest()
    if not path.exists():
        return {'active': 'default', 'projects': {
            'default': {'id': 'default', 'name': 'Personal', 'settings': {}}}}
    with path.open(encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data.get('projects'), dict) or 'default' not in data['projects']:
        raise ValueError('Invalid workspaces.json; restore the project registry before continuing')
    return data


def _write(data):
    from aria import data_dir
    data_dir.write_json_atomic(str(_manifest()), data)


def selected():
    with _LOCK:
        data = _read()
        project = data.get('active', 'default')
        return project if project in data['projects'] else 'default'


def current():
    return _BOUND.get() or selected()


def list_projects():
    with _LOCK:
        return list(_read()['projects'].values())


def get(project=None):
    with _LOCK:
        return dict(_read()['projects'][project or current()])


def create(name):
    name = str(name).strip()
    if not name or len(name) > 100:
        raise ValueError('Project names must contain 1–100 characters')
    project = uuid.uuid4().hex[:12]
    with _LOCK:
        data = _read()
        data['projects'][project] = {'id': project, 'name': name, 'settings': {}}
        _write(data)
    return project


def select(project):
    with _LOCK:
        data = _read()
        if project not in data['projects']:
            raise ValueError('Unknown project')
        data['active'] = project
        _write(data)


def update_settings(settings, project=None):
    with _LOCK:
        data = _read()
        entry = data['projects'][project or current()]
        entry['settings'] = {**entry.get('settings', {}), **settings}
        _write(data)


@contextlib.contextmanager
def use(project):
    if not _ID.fullmatch(str(project)):
        raise ValueError('Invalid project id')
    # Validate existence before any file can be opened under this id.
    get(project)
    token = _BOUND.set(project)
    try:
        yield
    finally:
        _BOUND.reset(token)


def path(name, legacy=None):
    """Return a project-owned file; callers may supply the original default path."""
    from aria import config
    project = current()
    if project == 'default':
        return str(legacy or Path(config.WORKSPACE_DIR) / name)
    if not _ID.fullmatch(project):
        raise ValueError('Invalid project id')
    root = Path(config.WORKSPACE_DIR) / 'projects' / project
    root.mkdir(parents=True, exist_ok=True)
    dest = root / name
    if not dest.resolve().is_relative_to(root.resolve()):
        raise ValueError('Project path escaped its storage directory')
    return str(dest)


def load_history():
    try:
        with open(path('conversation_context.json'), encoding='utf-8') as stream:
            value = json.load(stream)
        return value if isinstance(value, list) else []
    except FileNotFoundError:
        return []


def save_history(history):
    from aria import data_dir
    destination = path('conversation_context.json')
    data_dir.write_json_atomic(destination, history)


def file_allowed(filename):
    """Optional project roots restrict file tools; an empty list keeps legacy access."""
    roots = get().get('settings', {}).get('file_roots') or []
    target = Path(os.path.expandvars(os.path.expanduser(str(filename)))).resolve()
    return not roots or any(target.is_relative_to(Path(r).expanduser().resolve()) for r in roots)


def resolve_workspace_file(filename, legacy=None):
    """Resolve a relative file name once, enforcing storage and project roots.

    Recognize Windows paths on every platform; stripping a leading slash or
    joining a drive-qualified name does not make it a workspace-relative path.
    Existing symlinks/junctions must also remain inside the workspace.
    """
    name = str(filename or '').strip().replace('\\', '/')
    windows = PureWindowsPath(name)
    relative = Path(name)
    if (not name or name == '.' or '\x00' in name or ':' in name
            or windows.drive or windows.root or relative.is_absolute()
            or '..' in relative.parts):
        raise ValueError('Supply a relative file name inside the project workspace')
    root = Path(path('', legacy)).resolve()
    destination = (root / relative).resolve()
    if destination == root or not destination.is_relative_to(root):
        raise ValueError('File is outside the project workspace')
    if not file_allowed(destination):
        raise ValueError('File is outside this project’s configured roots')
    return str(destination)
