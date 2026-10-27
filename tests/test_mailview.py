"""Read-only IMAP integration with MIME and header regression fixtures."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('mailview', Path(__file__).parents[1] / 'aria' / 'mailview.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
RAW = b'From: =?utf-8?Q?Plex_Team?= <hello@example.com>\r\nSubject: =?UTF-8?Q?See_what=E2=80=99s_trending?=\r\nDate: Tue, 27 Oct 2026 07:26:04 +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello Alek.'


class IMAP:
    def __init__(self): self.calls = []; self.closed = False
    def select(self, name, readonly=False):
        assert name == 'INBOX' and readonly
        return 'OK', []
    def uid(self, operation, *args):
        self.calls.append((operation, args))
        if operation == 'SEARCH': return 'OK', [b'10 11']
        assert 'BODY.PEEK' in args[1]
        return 'OK', [(b'11 (FLAGS (\\Seen) BODY[] {200}', RAW), b')']
    def logout(self): self.closed = True


def test_encoded_headers_and_read_only_list():
    client = IMAP()
    result = m.fetch_messages(connect=lambda: (client, None))
    assert result['ok'] and client.closed
    assert [row['uid'] for row in result['messages']] == ['11', '10']
    row = result['messages'][0]
    assert row['subject'] == 'See what’s trending'
    assert row['sender'] == 'Plex Team' and row['address'] == 'hello@example.com'
    assert row['date'] == '2026-10-27T07:26:04+00:00'
    assert row['unread'] is False


def test_open_message_and_invalid_uid():
    client = IMAP()
    result = m.fetch_message('11', connect=lambda: (client, None))
    assert result['ok'] and result['message']['body'] == 'Hello Alek.'
    assert client.closed
    assert not m.fetch_message('11 UID STORE 1:*')['ok']


def test_unconfigured_and_bad_header_are_displayable():
    result = m.fetch_messages(connect=lambda: (None, 'Connect Gmail first.'))
    assert not result['ok'] and result['messages'] == []
    assert m.decode_mail_header('line one\r\n  line two') == 'line one line two'
    assert m.decode_mail_header('=?unknown?b?SGVsbG8=?=') == 'Hello'


def test_html_message_is_plain_text_and_does_not_load_images():
    raw = b'Content-Type: text/html; charset=utf-8\r\n\r\n<html><head><style>bad css</style></head><body><p>Hello &amp; goodbye</p><img src="https://tracker.invalid"><script>bad()</script><p>Next line</p></body></html>'
    text = m.message_text(m.email.message_from_bytes(raw))
    assert 'Hello & goodbye' in text and 'Next line' in text
    assert 'bad' not in text and 'https://' not in text and '<' not in text


def _hdr(sender, subject, date):
    return (f'Date: {date}\r\nFrom: {sender}\r\n'
            f'Subject: {subject}\r\n\r\n').encode()


class BatchIMAP:
    """A server that answers a UID-set FETCH with per-message tuples
    carrying UID tokens — the shape real IMAP servers return."""
    def __init__(self): self.fetch_calls = []
    def select(self, name, readonly=False): return 'OK', []
    def uid(self, operation, *args):
        if operation == 'SEARCH': return 'OK', [b'101 102']
        assert operation == 'FETCH'
        self.fetch_calls.append(args)
        return 'OK', [
            (b'2 (UID 102 FLAGS () BODY[HEADER.FIELDS (DATE FROM SUBJECT)] {120}',
             _hdr('Sam <sam@example.com>', 'Dinner Thursday?', 'Tue, 27 Oct 2026 09:00:00 +0000')),
            b')',
            (b'1 (UID 101 FLAGS (\\Seen) BODY[HEADER.FIELDS (DATE FROM SUBJECT)] {118}',
             _hdr('Bank <noreply@bank.example>', 'Statement ready', 'Mon, 26 Oct 2026 09:00:00 +0000')),
            b')']
    def status(self, *args): return 'OK', [b'INBOX (UNSEEN 1)']
    def logout(self): pass


def test_fetch_messages_batches_one_fetch():
    client = BatchIMAP()
    result = m.fetch_messages(limit=30, connect=lambda: (client, None))
    assert result['ok'] and result['unread_total'] == 1
    assert len(client.fetch_calls) == 1                 # one round-trip
    assert client.fetch_calls[0][0] == b'102,101'       # the whole set
    rows = result['messages']
    assert [r['uid'] for r in rows] == ['102', '101']    # newest first
    assert rows[0]['subject'] == 'Dinner Thursday?' and rows[0]['unread']
    assert rows[1]['subject'] == 'Statement ready' and not rows[1]['unread']


def test_imap_error_closes_connection():
    client = IMAP()
    client.uid = lambda *args: ('NO', [])
    result = m.fetch_messages(connect=lambda: (client, None))
    assert not result['ok'] and client.closed
