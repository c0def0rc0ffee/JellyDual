"""
<summary>
Shared helpers for Jellyfin Dual Play.
</summary>
"""

import base64
import json
import re
import urllib.error
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'service.jellyfin.dualplay'

# Matches the Jellyfin item id inside a resolved direct-stream URL, e.g.
#   http://server:8096/Videos/1a2b.../stream?...
#   http://server:8096/Items/1a2b.../Download?...
JELLYFIN_ID_RE = re.compile(
    r'/(?:Videos|Items|Audio)/'
    r'([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
)

JELLYFIN_PLUGIN_PLAY = 'plugin://plugin.video.jellyfin/?mode=play&id=%s'


MAX_DEVICES = 10  # fixed slots: Kodi settings cannot grow a list at runtime

# InfoLabel that reads the screensaver flag on a remote box over JSON-RPC.
SCREENSAVER_LABEL = 'Window(home).Property(VideoScreensaverRunning)'


def addon():
    return xbmcaddon.Addon(ADDON_ID)


def setting_str(key, default=''):
    """Read a string setting that may not exist (older/newer settings.xml)."""
    try:
        return addon().getSettingString(key) or default
    except Exception:
        return default


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] %s' % (ADDON_ID, msg), level)


def notify(msg, ms=4000, force=False):
    try:
        if force or addon().getSettingBool('notify'):
            xbmcgui.Dialog().notification(
                'Dual Play', msg, xbmcgui.NOTIFICATION_INFO, ms, False)
    except Exception:
        pass


def seconds_to_time(seconds):
    """Kodi's Player.Seek wants an hours/minutes/seconds/milliseconds object."""
    if seconds < 0:
        seconds = 0
    total_ms = int(round(seconds * 1000))
    return {
        'hours': total_ms // 3600000,
        'minutes': (total_ms // 60000) % 60,
        'seconds': (total_ms // 1000) % 60,
        'milliseconds': total_ms % 1000,
    }


def time_to_seconds(obj):
    if not obj:
        return 0.0
    return (obj.get('hours', 0) * 3600
            + obj.get('minutes', 0) * 60
            + obj.get('seconds', 0)
            + obj.get('milliseconds', 0) / 1000.0)


def local_rpc(method, params=None):
    """Call JSON-RPC on this Kodi instance."""
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        payload['params'] = params
    try:
        raw = xbmc.executeJSONRPC(json.dumps(payload))
        return json.loads(raw)
    except Exception as exc:  # pragma: no cover
        log('local_rpc %s failed: %s' % (method, exc), xbmc.LOGERROR)
        return {}


def local_active_player():
    """playerid of this box's video/audio player, or None."""
    for p in local_rpc('Player.GetActivePlayers').get('result') or []:
        if p.get('type') in ('video', 'audio'):
            return p.get('playerid')
    return None


def local_set_playing(playing):
    """Play or pause this box explicitly.

    xbmc.Player().pause() is only a toggle, so using it to "resume" also
    pauses if the user happened to pause during the handshake. PlayPause with
    an explicit play flag always lands in the state we asked for.
    """
    pid = local_active_player()
    if pid is None:
        return False
    local_rpc('Player.PlayPause', {'playerid': pid, 'play': bool(playing)})
    return True


class PeerError(Exception):
    pass


class Peer(object):
    """Thin JSON-RPC client for the follower box."""

    def __init__(self, host, port, user='', password='', timeout=6, name=''):
        self.name = (name or '').strip()
        self.host = (host or '').strip()
        # Tolerate the user pasting "123.123.123.50:8080" or a full URL.
        if self.host.startswith('http://'):
            self.host = self.host[7:]
        elif self.host.startswith('https://'):
            self.host = self.host[8:]
        # Drop any path pasted along with it ("123.123.123.50:8080/jsonrpc").
        self.host = self.host.split('/', 1)[0]
        if ':' in self.host and not self.host.startswith('['):
            head, _, tail = self.host.rpartition(':')
            if tail.isdigit():
                self.host, port = head, int(tail)
        self.port = int(port)
        self.user = user or ''
        self.password = password or ''
        self.timeout = timeout

    @property
    def url(self):
        return 'http://%s:%d/jsonrpc' % (self.host, self.port)

    @property
    def configured(self):
        return bool(self.host)

    @property
    def label(self):
        """What to call this box in logs and notifications."""
        return self.name or self.host

    def call(self, method, params=None, timeout=None):
        if not self.configured:
            raise PeerError('No follower address configured')
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
        if params is not None:
            payload['params'] = params
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(self.url, data=data)
        req.add_header('Content-Type', 'application/json')
        if self.user or self.password:
            token = base64.b64encode(
                ('%s:%s' % (self.user, self.password)).encode('utf-8')
            ).decode('ascii')
            req.add_header('Authorization', 'Basic %s' % token)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise PeerError('Follower rejected the username/password')
            raise PeerError('Follower returned HTTP %s' % exc.code)
        except Exception as exc:
            raise PeerError('Cannot reach follower at %s:%s (%s)'
                            % (self.host, self.port, exc))
        if 'error' in body:
            raise PeerError(str(body['error']))
        return body.get('result')

    # -- convenience wrappers -------------------------------------------------

    def ping(self):
        return self.call('JSONRPC.Ping')

    def notify_cmd(self, action, data=None):
        """Announce an imminent command so the peer's own addon knows the
        matching player event is mirrored, not a user action.

        Also used for group housekeeping messages ('left', 'joined'), which
        carry the sending box's name in data rather than driving its player.
        """
        params = {'sender': ADDON_ID, 'message': action}
        if data is not None:
            params['data'] = data
        self.call('JSONRPC.NotifyAll', params)

    def now_playing(self, timeout=3):
        """What this box is playing, or None. Used for late-joining a session."""
        pid = self.active_player()
        if pid is None:
            return None
        flags = self.call('XBMC.GetInfoLabels',
                          {'labels': [SCREENSAVER_LABEL]},
                          timeout=timeout) or {}
        if flags.get(SCREENSAVER_LABEL) == 'true':
            return None
        props = self.call('Player.GetProperties',
                          {'playerid': pid,
                           'properties': ['time', 'totaltime', 'speed']},
                          timeout=timeout)
        item = self.call('Player.GetItem',
                         {'playerid': pid, 'properties': ['file']},
                         timeout=timeout) or {}
        path = (item.get('item') or {}).get('file') or ''
        if not path:
            return None
        return {
            'path': path,
            'time': time_to_seconds(props.get('time')),
            'total': time_to_seconds(props.get('totaltime')),
            'speed': props.get('speed', 0),
        }

    def friendly_name(self):
        try:
            res = self.call('Application.GetProperties', {'properties': ['name', 'version']})
            v = res.get('version', {})
            return '%s %s.%s' % (res.get('name', 'Kodi'), v.get('major', '?'), v.get('minor', '?'))
        except PeerError:
            return 'Kodi'

    def active_player(self):
        res = self.call('Player.GetActivePlayers') or []
        for p in res:
            if p.get('type') in ('video', 'audio'):
                return p.get('playerid')
        return None

    def stop(self):
        pid = self.active_player()
        if pid is not None:
            self.call('Player.Stop', {'playerid': pid})

    def open_file(self, path):
        return self.call(
            'Player.Open',
            {'item': {'file': path}, 'options': {'resume': False}},
            timeout=20,
        )

    def set_playing(self, playing):
        pid = self.active_player()
        if pid is None:
            return False
        self.call('Player.PlayPause', {'playerid': pid, 'play': bool(playing)})
        return True

    def seek(self, seconds):
        pid = self.active_player()
        if pid is None:
            return False
        self.call('Player.Seek',
                  {'playerid': pid, 'value': {'time': seconds_to_time(seconds)}})
        return True

    def position(self):
        pid = self.active_player()
        if pid is None:
            return None
        res = self.call('Player.GetProperties',
                        {'playerid': pid, 'properties': ['time', 'totaltime', 'speed']})
        return {
            'time': time_to_seconds(res.get('time')),
            'total': time_to_seconds(res.get('totaltime')),
            'speed': res.get('speed', 0),
        }


def peer_from_settings():
    a = addon()
    return Peer(
        a.getSettingString('peer_host'),
        a.getSettingInt('peer_port'),
        a.getSettingString('peer_user'),
        a.getSettingString('peer_pass'),
    )


def group_from_settings():
    """Sync group: the other boxes, from the numbered device slots.

    Each slot has its own name and address. Entries may carry ":port" for a
    box on a non-standard port; otherwise the shared port setting applies, and
    all boxes share the username/password.
    """
    a = addon()
    port = a.getSettingInt('peer_port')
    user = a.getSettingString('peer_user')
    pw = a.getSettingString('peer_pass')
    group = []
    for n in range(1, MAX_DEVICES + 1):
        host = setting_str('dev%d_host' % n)
        p = Peer(host, port, user, pw, name=setting_str('dev%d_name' % n))
        if p.configured:
            group.append(p)
    if not group:
        # Carry over the single comma-separated field used by 2.0.0.
        for tok in re.split(r'[\s,;]+', setting_str('peer_list').strip()):
            if tok:
                p = Peer(tok, port, user, pw)
                if p.configured:
                    group.append(p)
    return group


def local_device_name():
    """What this box calls itself in the group."""
    name = setting_str('device_name').strip()
    if name:
        return name
    try:
        return xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    except Exception:
        return 'Kodi'


def local_open(path):
    """Start playing something on this box (used when joining a session)."""
    local_rpc('Player.Open', {'item': {'file': path}, 'options': {'resume': False}})


def local_seek(seconds):
    pid = local_active_player()
    if pid is None:
        return False
    local_rpc('Player.Seek',
              {'playerid': pid, 'value': {'time': seconds_to_time(seconds)}})
    return True


def local_position():
    """This box's own play position, or None."""
    pid = local_active_player()
    if pid is None:
        return None
    res = local_rpc('Player.GetProperties',
                    {'playerid': pid,
                     'properties': ['time', 'totaltime', 'speed']}).get('result') or {}
    return {
        'time': time_to_seconds(res.get('time')),
        'total': time_to_seconds(res.get('totaltime')),
        'speed': res.get('speed', 0),
    }


def resolve_playable_path():
    """Work out a path for the currently playing item that the follower can open.

    Preference order:
      1. A plugin:// URL (identical on both boxes when both use the same addon).
      2. A Jellyfin direct-stream URL -> rebuilt as a plugin:// play URL so the
         follower opens it through its own Jellyfin session, not the leader's.
      3. Whatever path Kodi reports (library items on shared SMB/NFS/HTTP paths
         are the same string on both boxes).
    """
    path = ''
    pid = local_active_player()
    if pid is not None:
        item = local_rpc('Player.GetItem', {
            'playerid': pid,
            'properties': ['file', 'title', 'uniqueid'],
        }).get('result', {}).get('item', {})
        path = item.get('file') or ''

    if not path:
        try:
            path = xbmc.Player().getPlayingFile() or ''
        except Exception:
            path = ''

    return normalize_path(path)


def normalize_path(path):
    """Turn a path into one another box can open.

    A resolved Jellyfin direct-stream URL is rebuilt as a plugin:// play URL so
    the other box streams through its own Jellyfin session rather than
    piggy-backing on this one's. Everything else passes through.
    """
    if not path:
        return ''

    if '/addons/screensaver.' in path.replace('\\', '/'):
        # A clip played by a video screensaver is never something to mirror.
        return ''

    if path.startswith('plugin://'):
        return path

    match = JELLYFIN_ID_RE.search(path)
    if match:
        # Jellyfin for Kodi stores ids without dashes.
        return JELLYFIN_PLUGIN_PLAY % match.group(1).replace('-', '').lower()

    return path
