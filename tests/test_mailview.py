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


def test_imap_error_closes_connection():
    client = IMAP()
    client.uid = lambda *args: ('NO', [])
    result = m.fetch_messages(connect=lambda: (client, None))
    assert not result['ok'] and client.closed
