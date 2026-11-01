"""Clean-install acceptance without contacting providers or starting microphones."""
from __future__ import annotations

import json
import os
import tempfile
import socket
import ipaddress


def main():
    # Diagnostic state is temporary, including API-key placeholders. No real
    # credentials, memories or project registry are read or changed.
    # Background UI cards also stay offline during acceptance.
    original_connect, original_lookup = socket.socket.connect, socket.getaddrinfo
    def local(host):
        if host in (None, 'localhost'): return True
        try: return ipaddress.ip_address(host).is_loopback
        except ValueError: return False
    def connect(sock, address):
        if isinstance(address, tuple) and not local(address[0]):
            raise OSError('Diagnostics are offline')
        return original_connect(sock, address)
    def lookup(host, port, *args, **kwargs):
        if not local(host): raise OSError('Diagnostics are offline')
        return original_lookup(host, port, *args, **kwargs)
    socket.socket.connect, socket.getaddrinfo = connect, lookup
    with tempfile.TemporaryDirectory(prefix='aria-diagnostics-') as root:
        os.environ['ARIA_DATA_ROOT'] = root
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from aria import config, memory, data_dir
        config.ROOT_DIR = data_dir._ROOT = root
        config.WORKSPACE_DIR = os.path.join(root, 'workspace')
        config.SETTINGS_FILE = os.path.join(config.WORKSPACE_DIR, 'settings.json')
        os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
        config.settings_save({'resource_profile':'minimal','startup_briefing':False})
        memory.DB_PATH = os.path.join(config.WORKSPACE_DIR, 'aria_memory.db')
        memory.init_databases()
        import aria.main as app_main
        from PyQt6.QtWidgets import QApplication
        from aria.dashboard import create_dashboard
        from aria.work_center import create_work_center
        app = QApplication.instance() or QApplication([])
        window = create_dashboard(app_main._qt_callbacks())
        window.show(); app.processEvents(); window.render_once()
        center = create_work_center(window, app_main._qt_callbacks())
        center.show(); app.processEvents(); center.refresh()
        from PyQt6.QtCore import QTimer, QEvent
        for timer in window.findChildren(QTimer): timer.stop()
        center.close(); window.close(); app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        from aria.tools.dispatch import get_registered_tools
        checks = {'status':'ok', 'database':True, 'native_dashboard':True,
                  'work_center':True, 'tools':len(get_registered_tools())}
        if os.name == 'nt':
            from aria.comclient import ComHostClient
            client = ComHostClient()
            try:
                result = client.request('ping', timeout=15)
                checks['com_helper'] = bool(result)
            finally:
                client.close()
        import gc
        gc.collect()
        print(json.dumps(checks), flush=True)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
