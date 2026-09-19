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
    """
    <summary>
    The add-on's own xbmcaddon.Addon, fetched fresh each call so a settings change is seen straight away.
    </summary>
    <returns>An Addon for ADDON_ID.</returns>
    """
    return xbmcaddon.Addon(ADDON_ID)


def setting_str(key, default=''):
    """
    <summary>
    Read a string setting that may not exist (older/newer settings.xml).
    </summary>
    """
    try:
        return addon().getSettingString(key) or default
    except Exception:
        return default


def log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to Kodi's log, prefixed with the add-on id.
    </summary>
    <param name="msg">Text to log.</param>
    <param name="level">Kodi log level, info by default.</param>
    """
    xbmc.log('[%s] %s' % (ADDON_ID, msg), level)


def notify(msg, ms=4000, force=False):
    """
    <summary>
    Show a Dual Play toast, but only when the notify setting is on unless forced.
    </summary>
    <param name="msg">The toast text.</param>
    <param name="ms">How long it stays up, in milliseconds.</param>
    <param name="force">Show it even with the notify setting off; used for a departure notice.</param>
    <remarks>
    Any failure is swallowed so a toast can never break a handshake.
    </remarks>
    """
    try:
        if force or addon().getSettingBool('notify'):
            xbmcgui.Dialog().notification(
                'Dual Play', msg, xbmcgui.NOTIFICATION_INFO, ms, False)
    except Exception:
        pass


def seconds_to_time(seconds):
    """
    <summary>
    Kodi's Player.Seek wants an hours/minutes/seconds/milliseconds object.
    </summary>
    """
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
    """
    <summary>
    Seconds as a float from a Kodi time object, 0.0 for None or an empty one.
    </summary>
    <param name="obj">A dict with hours, minutes, seconds and milliseconds, any of them missing.</param>
    <returns>The total in seconds.</returns>
    """
    if not obj:
        return 0.0
    return (obj.get('hours', 0) * 3600
            + obj.get('minutes', 0) * 60
            + obj.get('seconds', 0)
            + obj.get('milliseconds', 0) / 1000.0)


def local_rpc(method, params=None):
    """
    <summary>
    Call JSON-RPC on this Kodi instance.
    </summary>
    """
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
    """
    <summary>
    playerid of this box's video/audio player, or None.
    </summary>
    """
    for p in local_rpc('Player.GetActivePlayers').get('result') or []:
        if p.get('type') in ('video', 'audio'):
            return p.get('playerid')
    return None


def local_set_playing(playing):
    """
    <summary>
    Play or pause this box explicitly.
    </summary>
    <remarks>
    xbmc.Player().pause() is only a toggle, so using it to "resume" also
    pauses if the user happened to pause during the handshake. PlayPause with
    an explicit play flag always lands in the state we asked for.
    </remarks>
    """
    pid = local_active_player()
    if pid is None:
        return False
    local_rpc('Player.PlayPause', {'playerid': pid, 'play': bool(playing)})
    return True


class PeerError(Exception):
    """
    <summary>
    Raised by Peer when a box cannot be reached, refuses the login or returns a JSON-RPC error.
    </summary>
    """
    pass


class Peer(object):
    """
    <summary>
    Thin JSON-RPC client for the follower box.
    </summary>
    """

    def __init__(self, host, port, user='', password='', timeout=6, name=''):
        """
        <summary>
        Parse the address and keep the login; nothing is contacted yet.
        </summary>
        <param name="host">Host name, IP or a pasted URL: a scheme and any path are stripped, and a ':port' suffix overrides the port argument.</param>
        <param name="port">The JSON-RPC port when the host does not carry one.</param>
        <param name="user">Basic auth user name, or empty.</param>
        <param name="password">Basic auth password, or empty.</param>
        <param name="timeout">Seconds to wait per call unless a call gives its own.</param>
        <param name="name">Display name from the device slot; blank in Leader / Follower mode.</param>
        """
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
        """
        <summary>
        The box's JSON-RPC endpoint.
        </summary>
        <returns>An http URL.</returns>
        """
        return 'http://%s:%d/jsonrpc' % (self.host, self.port)

    @property
    def configured(self):
        """
        <summary>
        Whether an address was given at all.
        </summary>
        <returns>True when the host is not blank.</returns>
        """
        return bool(self.host)

    @property
    def label(self):
        """
        <summary>
        What to call this box in logs and notifications.
        </summary>
        """
        return self.name or self.host

    def call(self, method, params=None, timeout=None):
        """
        <summary>
        One JSON-RPC call to the box.
        </summary>
        <param name="method">The JSON-RPC method name.</param>
        <param name="params">The params member, or None to send none.</param>
        <param name="timeout">Seconds to wait, or None for the instance default.</param>
        <returns>The reply's result member, or None when there is none.</returns>
        <exception cref="PeerError">No address configured, a rejected login (401), any other HTTP error, an unreachable box, or an error member in the reply.</exception>
        """
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

    # == convenience wrappers =================================================

    def ping(self):
        """
        <summary>
        JSONRPC.Ping, as a cheap reachability check.
        </summary>
        <returns>The reply, 'pong' on a live box.</returns>
        """
        return self.call('JSONRPC.Ping')

    def notify_cmd(self, action, data=None):
        """
        <summary>
        Announce an imminent command so the peer's own addon knows the
        matching player event is mirrored, not a user action.
        </summary>
        <remarks>
        Also used for group housekeeping messages ('left', 'joined'), which
        carry the sending box's name in data rather than driving its player.
        </remarks>
        """
        params = {'sender': ADDON_ID, 'message': action}
        if data is not None:
            params['data'] = data
        self.call('JSONRPC.NotifyAll', params)

    def now_playing(self, timeout=3):
        """
        <summary>
        What this box is playing, or None. Used for late-joining a session.
        </summary>
        """
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
        """
        <summary>
        Kodi's application name and version on the box, for the settings test dialog.
        </summary>
        <returns>'<name> <major>.<minor>', or plain 'Kodi' when the box cannot be reached.</returns>
        """
        try:
            res = self.call('Application.GetProperties', {'properties': ['name', 'version']})
            v = res.get('version', {})
            return '%s %s.%s' % (res.get('name', 'Kodi'), v.get('major', '?'), v.get('minor', '?'))
        except PeerError:
            return 'Kodi'

    def active_player(self):
        """
        <summary>
        The playerid of the box's video or audio player.
        </summary>
        <returns>The id, or None when nothing is playing there.</returns>
        """
        res = self.call('Player.GetActivePlayers') or []
        for p in res:
            if p.get('type') in ('video', 'audio'):
                return p.get('playerid')
        return None

    def stop(self):
        """
        <summary>
        Stop whatever the box is playing; nothing is sent when it is idle.
        </summary>
        """
        pid = self.active_player()
        if pid is not None:
            self.call('Player.Stop', {'playerid': pid})

    def open_file(self, path):
        """
        <summary>
        Open a path on the box from the start.
        </summary>
        <param name="path">Something the box can open: a plugin:// URL or a shared path.</param>
        <returns>The call result.</returns>
        <remarks>
        Uses a 20 second timeout because Player.Open waits for the stream to resolve.
        </remarks>
        """
        return self.call(
            'Player.Open',
            {'item': {'file': path}, 'options': {'resume': False}},
            timeout=20,
        )

    def set_playing(self, playing):
        """
        <summary>
        Play or pause the box explicitly rather than toggling.
        </summary>
        <param name="playing">True to play, False to pause.</param>
        <returns>False when nothing is playing there, else True.</returns>
        """
        pid = self.active_player()
        if pid is None:
            return False
        self.call('Player.PlayPause', {'playerid': pid, 'play': bool(playing)})
        return True

    def seek(self, seconds):
        """
        <summary>
        Seek the box's player to an absolute position.
        </summary>
        <param name="seconds">Position from the start.</param>
        <returns>False when nothing is playing there, else True.</returns>
        """
        pid = self.active_player()
        if pid is None:
            return False
        self.call('Player.Seek',
                  {'playerid': pid, 'value': {'time': seconds_to_time(seconds)}})
        return True

    def position(self):
        """
        <summary>
        Where the box's player is.
        </summary>
        <returns>A dict of time and total in seconds and speed, or None when idle.</returns>
        """
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
    """
    <summary>
    The single follower box from the Leader / Follower settings.
    </summary>
    <returns>A Peer, unconfigured when peer_host is blank.</returns>
    """
    a = addon()
    return Peer(
        a.getSettingString('peer_host'),
        a.getSettingInt('peer_port'),
        a.getSettingString('peer_user'),
        a.getSettingString('peer_pass'),
    )


def group_from_settings():
    """
    <summary>
    Sync group: the other boxes, from the numbered device slots.
    </summary>
    <remarks>
    Each slot has its own name and address. Entries may carry ":port" for a
    box on a non-standard port; otherwise the shared port setting applies, and
    all boxes share the username/password.
    </remarks>
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
    """
    <summary>
    What this box calls itself in the group.
    </summary>
    """
    name = setting_str('device_name').strip()
    if name:
        return name
    try:
        return xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    except Exception:
        return 'Kodi'


def local_open(path):
    """
    <summary>
    Start playing something on this box (used when joining a session).
    </summary>
    """
    local_rpc('Player.Open', {'item': {'file': path}, 'options': {'resume': False}})


def local_seek(seconds):
    """
    <summary>
    Seek this box's player to an absolute position.
    </summary>
    <param name="seconds">Position from the start.</param>
    <returns>False when nothing is playing here, else True.</returns>
    """
    pid = local_active_player()
    if pid is None:
        return False
    local_rpc('Player.Seek',
              {'playerid': pid, 'value': {'time': seconds_to_time(seconds)}})
    return True


def local_position():
    """
    <summary>
    This box's own play position, or None.
    </summary>
    """
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
    """
    <summary>
    Work out a path for the currently playing item that the follower can open.
    </summary>
    <remarks>
    Preference order:
      1. A plugin:// URL (identical on both boxes when both use the same addon).
      2. A Jellyfin direct-stream URL -> rebuilt as a plugin:// play URL so the
         follower opens it through its own Jellyfin session, not the leader's.
      3. Whatever path Kodi reports (library items on shared SMB/NFS/HTTP paths
         are the same string on both boxes).
    </remarks>
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
    """
    <summary>
    Turn a path into one another box can open.
    </summary>
    <remarks>
    A resolved Jellyfin direct-stream URL is rebuilt as a plugin:// play URL so
    the other box streams through its own Jellyfin session rather than
    piggy-backing on this one's. Everything else passes through.
    </remarks>
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
