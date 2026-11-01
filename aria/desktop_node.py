"""Outbound desktop companion. Run python -m aria.desktop_node --help.

Files stay inside --root. Only explicitly enabled capabilities are advertised.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sqlite3
import threading
from urllib.parse import urlsplit


class DesktopAgent:
    def __init__(self, root, *, writes=False, controls=False, state_dir=None):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('The shared root must be an existing directory')
        self.writes, self.controls = writes, controls
        self.lock = threading.Lock()
        state = Path(state_dir or self.root / '.aria-companion')
        state.mkdir(parents=True, exist_ok=True)
        self.db = state / 'receipts.sqlite3'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS commands '
                         '(id TEXT PRIMARY KEY, fingerprint TEXT, state TEXT, result TEXT)')

    @property
    def capabilities(self):
        return ['system_info', 'list_files', 'read_file'] + (
            ['write_file'] if self.writes else []) + (
            ['read_screen', 'click', 'type', 'key', 'open_url'] if self.controls else [])

    def _path(self, value):
        dest = (self.root / str(value or '.')).resolve()
        if not dest.is_relative_to(self.root) or dest == self.db or self.db.parent in dest.parents:
            raise ValueError('Path is outside the shared files')
        return dest

    def execute(self, action, args, execution_id):
        if action not in self.capabilities:
            raise ValueError('Capability is not enabled on this computer')
        if not execution_id or len(execution_id) > 512:
            raise ValueError('A bounded execution id is required')
        fingerprint = hashlib.sha256(json.dumps([action, args], sort_keys=True).encode()).hexdigest()
        with self.lock, sqlite3.connect(self.db) as conn:
            row = conn.execute('SELECT fingerprint,state,result FROM commands WHERE id=?', (execution_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError('Execution id reused with different arguments')
                if row[1] == 'done':
                    return json.loads(row[2])
                raise RuntimeError('Earlier command outcome is uncertain; check this computer before retrying')
            conn.execute('INSERT INTO commands VALUES(?,?,\'executing\',NULL)', (execution_id, fingerprint))
            conn.commit()
            result = self._perform(action, args)
            conn.execute('UPDATE commands SET state=\'done\',result=? WHERE id=?', (json.dumps(result), execution_id))
            conn.commit()
            return result

    def _perform(self, action, args):
        if action == 'system_info':
            return {'platform': platform.platform(), 'name': platform.node(), 'root': str(self.root)}
        if action == 'list_files':
            return {'files': [{'name': p.name, 'directory': p.is_dir()} for p in
                              sorted(self._path(args.get('path', '.')).iterdir())[:200]
                              if p.name != '.aria-companion']}
        if action == 'read_file':
            path = self._path(args.get('path'))
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('File is larger than the 2 MiB text limit')
            return {'path': str(path), 'content': path.read_text(encoding='utf-8')}
        if action == 'write_file':
            import tempfile
            path, content = self._path(args.get('path')), str(args.get('content', ''))
            if len(content.encode()) > 2 * 1024 * 1024:
                raise ValueError('Content is larger than the 2 MiB text limit')
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                    stream.write(content); stream.flush(); os.fsync(stream.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
            return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if action == 'open_url':
            import webbrowser
            url = str(args.get('url', ''))
            if urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).netloc:
                raise ValueError('Only absolute http(s) URLs are supported')
            return {'opened': bool(webbrowser.open(url))}
        import pyautogui
        if action == 'read_screen':
            screen = pyautogui.screenshot(); screen.thumbnail((1600, 1000))
            output = io.BytesIO(); screen.save(output, format='PNG')
            return {'image_base64': base64.b64encode(output.getvalue()).decode(), 'mime': 'image/png'}
        if action == 'click': pyautogui.click(int(args['x']), int(args['y']))
        elif action == 'type': pyautogui.write(str(args.get('text', ''))[:10000], interval=.01)
        elif action == 'key':
            keys = args.get('keys') or []
            if not isinstance(keys, list) or not 1 <= len(keys) <= 4:
                raise ValueError('Supply 1–4 key names')
            pyautogui.hotkey(*[str(k) for k in keys])
        return {'performed': action}


async def connect(url, key, name, agent, *, ca_file=None, stop=None):
    import aiohttp
    import ssl
    parsed = urlsplit(url)
    if parsed.scheme not in {'ws', 'wss'} or not parsed.hostname:
        raise ValueError('Use a ws:// or wss:// bridge URL ending in /node/ws')
    if parsed.scheme == 'ws' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Use wss:// for a companion on another computer')
    tls = ssl.create_default_context(cafile=ca_file) if parsed.scheme == 'wss' else None
    while stop is None or not stop.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, ssl=tls, heartbeat=20, max_msg_size=4*1024*1024) as ws:
                    await ws.send_json({'type':'hello','node':name,'key':key,'kind':'desktop',
                                        'capabilities':agent.capabilities})
                    welcome = await ws.receive_json(timeout=15)
                    if welcome.get('type') != 'welcome' or not welcome.get('ok'):
                        raise ValueError('Pairing rejected')
                    print(f'Connected as {name}; capabilities: {", ".join(agent.capabilities)}', flush=True)
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT: continue
                        command = json.loads(msg.data)
                        if command.get('type') != 'command': continue
                        try:
                            args = dict(command.get('args') or {})
                            execution_id = str(args.pop('_execution_id', command['id']))
                            data = await asyncio.to_thread(agent.execute, command['action'], args, execution_id)
                            await ws.send_json({'type':'result','id':command['id'],'ok':True,'data':data})
                        except Exception as exc:
                            await ws.send_json({'type':'result','id':command['id'],'ok':False,'error':str(exc)})
        except (OSError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
            print(f'Connection unavailable ({type(exc).__name__}); reconnecting in 5 seconds', flush=True)
            await asyncio.sleep(5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True); parser.add_argument('--name', required=True)
    parser.add_argument('--root', required=True); parser.add_argument('--key-file')
    parser.add_argument('--ca-file', help='Trusted bridge certificate PEM for self-signed HTTPS')
    parser.add_argument('--allow-writes', action='store_true'); parser.add_argument('--allow-controls', action='store_true')
    args = parser.parse_args(argv)
    key = Path(args.key_file).read_text().strip() if args.key_file else os.environ.get('ARIA_NODE_KEY', '')
    if not key: parser.error('Set ARIA_NODE_KEY or supply --key-file')
    agent = DesktopAgent(args.root, writes=args.allow_writes, controls=args.allow_controls)
    try: asyncio.run(connect(args.url, key, args.name, agent, ca_file=args.ca_file))
    except KeyboardInterrupt: pass


if __name__ == '__main__': main()
