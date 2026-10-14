"""
<summary>
Checks over the Peer JSON-RPC client and the dualplay helpers: address parsing, the auth header, the time conversions and every error path of Peer.call against a throwaway HTTP server on loopback.
</summary>
<remarks>
No Kodi and no network beyond 127.0.0.1 are needed. The server answers in whatever shape the current check asks for (a 401, an HTML page, a bare JSON null, an error member, a slow reply) so each PeerError message can be pinned down.
</remarks>
"""
import base64
import json
import os
import socket
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'service.jellyfin.dualplay')

xbmc = types.ModuleType('xbmc')
xbmc.LOGINFO = 1; xbmc.LOGWARNING = 2; xbmc.LOGERROR = 3; xbmc.LOGDEBUG = 0
xbmc.log = lambda *a, **k: None
xbmcaddon = types.ModuleType('xbmcaddon')
xbmcgui = types.ModuleType('xbmcgui')
for name, mod in [('xbmc', xbmc), ('xbmcaddon', xbmcaddon), ('xbmcgui', xbmcgui)]:
    sys.modules[name] = mod

sys.path.insert(0, ADDON_DIR + '/resources/lib')
from dualplay import (  # noqa: E402
    Peer, PeerError, jellyfin_plugin_play_id, seconds_to_time, time_to_seconds,
)

PASS = []


def ok(msg):
    """
    <summary>
    Record and print one passed check.
    </summary>
    <param name="msg">What passed.</param>
    """
    PASS.append(msg)
    print('  ok  %s' % msg)


def expect_error(label, fn, *needles):
    """
    <summary>
    Run fn and require a PeerError whose text carries every needle.
    </summary>
    <param name="label">Printed with the pass line.</param>
    <param name="fn">Callable expected to raise PeerError.</param>
    <param name="needles">Substrings the message must contain.</param>
    <returns>The error message.</returns>
    """
    try:
        fn()
    except PeerError as exc:
        text = str(exc)
        for needle in needles:
            assert needle in text, (label, text)
        ok('%s: %s' % (label, text))
        return text
    raise AssertionError('%s: no PeerError raised' % label)


print('\n== address parsing ==')
cases = [
    ('123.123.123.50', '123.123.123.50', 8080),
    ('123.123.123.50:9090', '123.123.123.50', 9090),
    ('http://123.123.123.50:9090/jsonrpc', '123.123.123.50', 9090),
    ('https://box.lan/', 'box.lan', 8080),
    (' box.lan ', 'box.lan', 8080),
    ('123.123.123.50:', '123.123.123.50', 8080),
    ('123.123.123.50:123456', '123.123.123.50:123456', 8080),
    (':9090', ':9090', 8080),
    ('fe80::1', 'fe80::1', 8080),
    ('[fe80::1]:9090', '[fe80::1]:9090', 8080),
    ('', '', 8080),
]
for given, host, port in cases:
    p = Peer(given, 8080)
    assert (p.host, p.port) == (host, port), (given, p.host, p.port)
ok('port suffix split only for a one to five digit tail with a host before it')
assert Peer('123.123.123.50:', 8080).url == 'http://123.123.123.50:8080/jsonrpc'
ok('a trailing colon is dropped rather than doubled into the URL')
assert Peer('fe80::1', 8080).url == 'http://fe80::1:8080/jsonrpc'
ok('an IPv6 literal passes through unchanged (unsupported, documented)')
assert not Peer('', 8080).configured and Peer('x', 8080).configured
assert Peer('x', 8080, name=' Bedroom ').label == 'Bedroom' and Peer('x', 8080).label == 'x'
ok('configured and label')

print('\n== time conversions ==')
for seconds in (0, 0.4, 59.999, 61.5, 3600, 5399.25):
    obj = seconds_to_time(seconds)
    assert abs(time_to_seconds(obj) - seconds) < 0.0006, (seconds, obj)
assert seconds_to_time(-5) == {'hours': 0, 'minutes': 0, 'seconds': 0, 'milliseconds': 0}
assert time_to_seconds(None) == 0.0 and time_to_seconds({}) == 0.0
ok('seconds_to_time and time_to_seconds round trip; negatives clamp; empty is zero')
HEX = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
assert jellyfin_plugin_play_id('plugin://plugin.video.jellyfin/?mode=play&id=' + HEX.upper()) == HEX
assert jellyfin_plugin_play_id('plugin://plugin.video.jellyfin/?mode=playlist&id=' + HEX) == ''
assert jellyfin_plugin_play_id('plugin://plugin.video.jellyfin/?mode=play&id=abc') == ''
ok('jellyfin_plugin_play_id: play URLs only, canonical id, junk rejected')

print('\n== Peer.call against a loopback server ==')
MODE = {'reply': 'ok'}


class Handler(BaseHTTPRequestHandler):
    """
    <summary>
    Answers every POST in the shape MODE['reply'] asks for.
    </summary>
    """
    def log_message(self, *a):
        """
        <summary>
        Silence the server log.
        </summary>
        <param name="a">Ignored.</param>
        """

    def do_POST(self):
        """
        <summary>
        Read the request and reply according to MODE: ok, 401, html, null, error, echoauth or slow.
        </summary>
        """
        length = int(self.headers.get('Content-Length', 0))
        request = json.loads(self.rfile.read(length).decode('utf-8'))
        mode = MODE['reply']
        if mode == '401':
            self.send_response(401)
            self.end_headers()
            return
        if mode == '500':
            self.send_response(500)
            self.end_headers()
            return
        if mode == 'slow':
            time.sleep(1.5)
        body = {'jsonrpc': '2.0', 'id': request.get('id'), 'result': 'pong'}
        content_type = 'application/json'
        if mode == 'html':
            raw = b'<html><body>Not Kodi</body></html>'
            content_type = 'text/html'
        elif mode == 'null':
            raw = b'null'
        elif mode == 'list':
            raw = b'[1, 2]'
        elif mode == 'error':
            raw = json.dumps({'jsonrpc': '2.0', 'id': 1,
                              'error': {'code': -32601, 'message': 'Method not found'}}).encode('utf-8')
        elif mode == 'echoauth':
            raw = json.dumps({'result': self.headers.get('Authorization', '')}).encode('utf-8')
        else:
            raw = json.dumps(body).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass            # the client gave up (the slow check)


# Threading, so the slow check's handler cannot hold up the request after it.
server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
probe = socket.socket()
probe.bind(('127.0.0.1', 0))
CLOSED = probe.getsockname()[1]
probe.close()

live = Peer('127.0.0.1', PORT)
assert live.ping() == 'pong'
ok('a live box answers pong')
t0 = time.time()
expect_error('connection refused', lambda: Peer('127.0.0.1', CLOSED).ping(), 'Cannot reach follower', str(CLOSED))
assert time.time() - t0 < 1.0
ok('a refused connection fails at once, no timeout paid')
MODE['reply'] = '401'
expect_error('401', lambda: Peer('127.0.0.1', PORT, 'kodi', 'x').ping(), 'rejected the username/password')
MODE['reply'] = '500'
expect_error('500', live.ping, 'HTTP 500')
MODE['reply'] = 'html'
text = expect_error('HTML page on that port', live.ping, 'not with JSON-RPC', str(PORT), 'Settings > Services > Control')
assert 'Expecting value' not in text
MODE['reply'] = 'null'
expect_error('bare JSON null', live.ping, 'unexpected reply', 'NoneType')
MODE['reply'] = 'list'
expect_error('JSON list', live.ping, 'unexpected reply', 'list')
MODE['reply'] = 'error'
expect_error('error member', live.ping, 'Method not found')
MODE['reply'] = 'ok'
expect_error('no address', lambda: Peer('', 8080).ping(), 'No follower address')

MODE['reply'] = 'echoauth'
awkward = 'p@ss:wörd/+=&?#"'
header = Peer('127.0.0.1', PORT, 'kodi', awkward).ping()
assert header.startswith('Basic ')
assert base64.b64decode(header.split()[1]).decode('utf-8') == 'kodi:' + awkward
ok('Basic auth header round trips a password with punctuation and a non ASCII letter')
assert Peer('127.0.0.1', PORT).ping() == ''
ok('no username and no password sends no Authorization header')

MODE['reply'] = 'slow'
t0 = time.time()
expect_error('ping with a short timeout', lambda: live.ping(timeout=0.3), 'Cannot reach follower', 'timed out')
elapsed = time.time() - t0
assert elapsed < 1.2, elapsed
ok('ping(timeout=0.3) gave up after %.2fs: the probe timeout is honoured' % elapsed)
MODE['reply'] = 'ok'
assert Peer('127.0.0.1', PORT, timeout=0.3).ping() == 'pong'
ok('the instance timeout applies when a call gives none')

server.shutdown()
print('\n%d checks passed' % len(PASS))
