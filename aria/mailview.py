"""Structured, read-only inbox data shared by the desktop and phone views."""
from __future__ import annotations

import email
import re
from datetime import timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser


def decode_mail_header(value):
    parts = []
    for text, charset in decode_header(str(value or '')):
        if isinstance(text, bytes):
            try:
                text = text.decode(charset or 'utf-8', errors='replace')
            except (LookupError, UnicodeError):
                text = text.decode('utf-8', errors='replace')
        parts.append(text)
    return ' '.join(''.join(parts).split())


def header_data(msg):
    sender, address = parseaddr(decode_mail_header(msg.get('From')))
    date = ''
    try:
        dt = parsedate_to_datetime(str(msg.get('Date', '')))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        date = dt.isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    return {'sender': sender or address or 'Unknown sender', 'address': address,
            'subject': decode_mail_header(msg.get('Subject')) or '(No subject)',
            'date': date}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def message_text(msg):
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_disposition() == 'attachment':
            continue
        kind = part.get_content_type()
        if kind not in ('text/plain', 'text/html'):
            continue
        payload = part.get_payload(decode=True) or b''
        try:
            text = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
        except LookupError:
            text = payload.decode('utf-8', errors='replace')
        (plain if kind == 'text/plain' else html).append(text)
    if plain:
        text = '\n\n'.join(plain)
    else:
        parser = _HTMLText(); parser.feed('\n'.join(html))
        text = ''.join(parser.parts)
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip()[:120000] or 'This message has no readable text.'


def _connect(connect):
    if connect is None:
        from aria.tools.builtins import _gmail_imap
        connect = _gmail_imap
    return connect()


def _raw(data):
    return next((part[1] for part in data or []
                 if isinstance(part, tuple) and isinstance(part[1], bytes)), b'')


def fetch_messages(limit=15, unread_only=False, connect=None):
    client, error = _connect(connect)
    if error:
        return {'ok': False, 'error': error, 'messages': []}
    try:
        typ, _ = client.select('INBOX', readonly=True)
        if typ != 'OK':
            raise RuntimeError('Could not open the inbox.')
        typ, data = client.uid('SEARCH', None, 'UNSEEN' if unread_only else 'ALL')
        if typ != 'OK':
            raise RuntimeError('Could not load the inbox.')
        ids = (data[0] or b'').split()[-max(1, min(50, int(limit))):][::-1]
        messages = []
        for uid in ids:
            typ, data = client.uid('FETCH', uid, '(FLAGS BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])')
            raw = _raw(data)
            if typ != 'OK' or not raw:
                continue
            row = header_data(email.message_from_bytes(raw))
            flags = b' '.join(part[0] for part in data if isinstance(part, tuple))
            row.update(uid=uid.decode('ascii'), unread=b'\\Seen' not in flags)
            messages.append(row)
        return {'ok': True, 'messages': messages}
    except Exception as exc:
        return {'ok': False, 'error': 'Could not read Gmail: ' + str(exc), 'messages': []}
    finally:
        try:
            client.logout()
        except Exception:
            pass


def fetch_message(uid, connect=None):
    uid = str(uid)
    if not re.fullmatch(r'[1-9][0-9]{0,19}', uid):
        return {'ok': False, 'error': 'Invalid message identifier.'}
    client, error = _connect(connect)
    if error:
        return {'ok': False, 'error': error}
    try:
        typ, _ = client.select('INBOX', readonly=True)
        if typ != 'OK':
            raise RuntimeError('Could not open the inbox.')
        typ, data = client.uid('FETCH', uid, '(BODY.PEEK[]<0.2097152>)')
        raw = _raw(data)
        if typ != 'OK' or not raw:
            raise RuntimeError('This message is no longer available. Refresh the inbox.')
        msg = email.message_from_bytes(raw)
        row = header_data(msg)
        row.update(uid=uid, body=message_text(msg), truncated=len(raw) >= 2097152)
        return {'ok': True, 'message': row}
    except Exception as exc:
        return {'ok': False, 'error': 'Could not open message: ' + str(exc)}
    finally:
        try:
            client.logout()
        except Exception:
            pass
