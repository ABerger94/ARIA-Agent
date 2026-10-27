"""Real Chromium smoke test of QR auto-login and the mobile dashboard.

WebEngine is a test-only dependency, not an ARIA runtime requirement.
"""
import importlib.util
import os
import sys
import threading
import time
import types
from pathlib import Path
from http.server import ThreadingHTTPServer

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = '--no-sandbox --disable-gpu --disable-dev-shm-usage'
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import pytest
pytest.importorskip('PyQt6.QtWebEngineWidgets', exc_type=ImportError)
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView

spec = importlib.util.spec_from_file_location('bridge_fixture', Path(__file__).with_name('test_bridge.py'))
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
bridge = fixture.bridge


def test_qr_mobile_controls_inbox_and_reopen(monkeypatch, tmp_path):
    fixture._setup_bridge()
    hud = types.ModuleType('aria.hud'); hud.CURRENT_STATE = 'idle'
    providers = types.ModuleType('aria.agent.providers'); providers.get_active_provider = lambda: 'Test engine'
    for name, mod in [('aria.hud', hud), ('aria.agent.providers', providers)]:
        monkeypatch.setitem(sys.modules, name, mod)
        parent, attr = name.rsplit('.', 1)
        monkeypatch.setattr(sys.modules[parent], attr, mod, raising=False)
    import aria.mailview as mail
    row = {'uid': '11', 'sender': 'Example Team', 'address': 'hello@example.com', 'subject': 'See what’s trending', 'date': '2026-10-27T07:26:04+00:00', 'unread': True}
    monkeypatch.setattr(mail, 'fetch_messages', lambda **kw: {'ok': True, 'messages': [row]})
    monkeypatch.setattr(mail, 'fetch_message', lambda uid: {'ok': True, 'message': dict(row, body='<script>not executable</script> Message body.')})
    bridge.set_chat_log_provider(lambda: [('12:00', 'ALEK', 'Hello'), ('12:01', 'A.R.I.A.', 'Ready to help.')])
    server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.BridgeHandler)
    monkeypatch.setattr(bridge, '_BRIDGE_SERVER', server)
    monkeypatch.setattr(bridge, '_BRIDGE_ACTUAL_SCHEME', 'http')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    app = QApplication.instance() or QApplication([])
    errors = []
    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            if level == self.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                errors.append(message)
    view = QWebEngineView(); page = Page(view); view.setPage(page); view.resize(390, 844); view.show()
    def evaluate(script):
        result = []
        page.runJavaScript(script, lambda value: result.append(value))
        until = time.monotonic() + 5
        while not result and time.monotonic() < until:
            app.processEvents(); time.sleep(.005)
        assert result, 'JavaScript evaluation timed out'
        return result[0]
    def wait(script):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            if evaluate(script): return
            app.processEvents(); time.sleep(.02)
        raise AssertionError('Browser condition not met: ' + script + '\n' + str(errors))
    base = f'http://127.0.0.1:{server.server_port}'
    key = bridge.create_pairing_url().split('?pair=')[1]
    try:
        view.load(QUrl(base + '/auto-login?pair=' + key))
        wait("document.getElementById('connection')?.textContent === 'Connected'")
        assert page.url().toString() == base + '/'
        assert not evaluate("!!document.querySelector('input[type=password]')")
        assert not evaluate("document.documentElement.scrollWidth > window.innerWidth")
        wait("document.getElementById('provider')?.textContent === 'Test engine'")
        view.grab().save(str(tmp_path / 'phone-control.png'))
        evaluate("switchTab('inbox');document.getElementById('mail-refresh').click()")
        wait("document.querySelectorAll('.mail-card').length === 1")
        evaluate("document.querySelector('.mail-card').click()")
        wait("document.getElementById('mail-body').textContent.includes('Message body')")
        assert evaluate("document.getElementById('mail-body').textContent.includes('<script>')")
        assert not evaluate("!!document.querySelector('#mail-body script')")
        view.grab().save(str(tmp_path / 'phone-inbox.png'))
        # A new navigation with the saved cookie opens the controls automatically.
        view.load(QUrl(base + '/'))
        wait("document.getElementById('connection')?.textContent === 'Connected'")
        assert not errors, errors
    finally:
        view.close(); page.deleteLater(); app.processEvents()
        server.shutdown(); server.server_close(); thread.join(timeout=2)
