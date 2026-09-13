"""
<summary>
Simulate the sync-group state machine against fake Kodi boxes.
</summary>
"""
import json
import sys
import time
import types

import os

ADDON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'service.jellyfin.dualplay')

xbmc = types.ModuleType('xbmc')
xbmc.LOGINFO = 1; xbmc.LOGWARNING = 2; xbmc.LOGERROR = 3; xbmc.LOGDEBUG = 0
xbmc.log = lambda *a, **k: None
xbmc.getInfoLabel = lambda k: 'KodiBox'
xbmc.executeJSONRPC = lambda s: '{}'


class Monitor(object):
    def waitForAbort(self, t=0):
        time.sleep(min(t, 0.01))
        return False

    def abortRequested(self):
        return False


xbmc.Monitor = Monitor
xbmc.Player = object

SETTINGS = {
    'enabled': True, 'role': 2, 'start_timeout': 3, 'lead_ms': 0,
    'drift_check': False, 'drift_interval': 120, 'drift_tolerance': 5,
    'stop_action': 0, 'notify': True, 'peer_port': 8080,
    'peer_user': '', 'peer_pass': '', 'peer_host': '', 'peer_list': '',
    'device_name': 'Lounge',
    'dev1_name': 'Bedroom', 'dev1_host': '123.123.123.60',
    'dev2_name': 'Kitchen', 'dev2_host': '123.123.123.70:8090',
}
for n in range(3, 11):
    SETTINGS['dev%d_name' % n] = ''
    SETTINGS['dev%d_host' % n] = ''

xbmcaddon = types.ModuleType('xbmcaddon')


class Addon(object):
    def __init__(self, *a):
        pass

    def getAddonInfo(self, k):
        return ADDON_DIR

    def getSettingBool(self, k):
        return bool(SETTINGS[k])

    def getSettingInt(self, k):
        return int(SETTINGS[k])

    def getSettingString(self, k):
        return str(SETTINGS[k])


xbmcaddon.Addon = Addon
xbmcgui = types.ModuleType('xbmcgui')
xbmcgui.NOTIFICATION_INFO = 0
NOTES = []


class Dialog(object):
    def notification(self, title, msg, *a, **k):
        NOTES.append(msg)


xbmcgui.Dialog = Dialog
WINDOW_PROPS = {}


class Window(object):
    def __init__(self, *a):
        pass

    def getProperty(self, k):
        return WINDOW_PROPS.get(k, '')


xbmcgui.Window = Window
xbmcvfs = types.ModuleType('xbmcvfs')
xbmcvfs.translatePath = lambda p: p
for name, mod in [('xbmc', xbmc), ('xbmcaddon', xbmcaddon),
                  ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs)]:
    sys.modules[name] = mod

sys.path.insert(0, ADDON_DIR + '/resources/lib')
sys.path.insert(0, ADDON_DIR)
import service                      # noqa: E402
from dualplay import PeerError, normalize_path  # noqa: E402

PASS = []


def ok(msg):
    PASS.append(msg)
    print('  ok  %s' % msg)


class FakeBox(object):
    """A remote Kodi box: records what was done to it."""

    def __init__(self, host, name=''):
        self.host, self.port, self.name = host, 8080, name
        self.calls = []
        self.playing = None      # dict(path, time, speed)
        self.down = False

    @property
    def configured(self):
        return True

    @property
    def label(self):
        return self.name or self.host

    def _rec(self, what):
        if self.down:
            raise PeerError('unreachable')
        self.calls.append(what)

    def notify_cmd(self, action, data=None):
        self._rec('announce:%s%s' % (action, ':' + json.dumps(data) if data else ''))

    def stop(self):
        self._rec('stop')
        self.playing = None

    def open_file(self, path):
        self._rec('open')
        self.playing = {'path': path, 'time': 0.0, 'total': 5400.0, 'speed': 1}

    def set_playing(self, v):
        self._rec('play' if v else 'pause')
        if self.playing:
            self.playing['speed'] = 1 if v else 0

    def seek(self, t):
        self._rec('seek:%.1f' % t)
        if self.playing:
            self.playing['time'] = t

    def position(self):
        if self.down:
            raise PeerError('unreachable')
        return dict(self.playing, total=self.playing['total']) if self.playing else None

    def now_playing(self, timeout=3):
        if self.down:
            raise PeerError('unreachable')
        return dict(self.playing) if self.playing else None

    def acts(self):
        return [c for c in self.calls if not c.startswith('announce')]


class FakePlayer(object):
    def __init__(self, playing=True):
        self.playing = playing
        self.t = 900.0

    def isPlaying(self):
        return self.playing

    def isPlayingVideo(self):
        return self.playing

    def isPlayingAudio(self):
        return False

    def getTime(self):
        return self.t


def fresh(stop_action=0):
    del NOTES[:]
    SETTINGS['stop_action'] = stop_action
    ctl = service.Controller()
    bed, kit = FakeBox('123.123.123.60', 'Bedroom'), FakeBox('123.123.123.70', 'Kitchen')
    ctl.peers = [bed, kit]
    return ctl, bed, kit


print('\n== settings: 10 device slots ==')
ctl, bed, kit = fresh()
assert ctl.me == 'Lounge', ctl.me
peers = service.group_from_settings()
assert [(p.label, p.host, p.port) for p in peers] == [
    ('Bedroom', '123.123.123.60', 8080), ('Kitchen', '123.123.123.70', 8090)], peers
ok('named slots parsed, per-slot :port honoured, blanks skipped')
SETTINGS['dev5_host'] = 'attic.local'
assert len(service.group_from_settings()) == 3
SETTINGS['dev5_host'] = ''
ok('a gap in the slots does not truncate the list')

print('\n== host starts, others follow ==')
ctl, bed, kit = fresh()
service.resolve_playable_path = lambda: 'plugin://plugin.video.jellyfin/?mode=play&id=abc'
local = []
service.local_set_playing = lambda v: local.append(v)
ctl.on_start(FakePlayer())
for b in (bed, kit):
    assert b.acts() == ['stop', 'open', 'pause', 'seek:900.0', 'play'], b.acts()
assert local == [False, True]
assert ctl.session_host and ctl.active_path
ok('host opens+syncs every member, holds itself, then releases')

print('\n== video screensaver clip is not hosted ==')
ctl, bed, kit = fresh()
ctl.on_peer_cmd('open')             # a pending group open must survive
WINDOW_PROPS['VideoScreensaverRunning'] = 'true'
ctl.on_start(FakePlayer())
WINDOW_PROPS.clear()
assert bed.calls == [] and kit.calls == [], (bed.calls, kit.calls)
assert ctl.active_path is None and not ctl.session_host
assert 'open' in ctl.expect, ctl.expect
ok('screensaver flag set -> no stop/open sent, pending open not consumed')
assert normalize_path('/storage/.kodi/addons/screensaver.dreadnought/clips/a.mp4') == ''
assert normalize_path('C:\\Kodi\\addons\\screensaver.dreadnought\\clips\\a.mp4') == ''
assert normalize_path('smb://nas/films/a.mkv') == 'smb://nas/films/a.mkv'
ok('normalize_path drops clips from a screensaver add-on folder, keeps films')

print('\n== a member does NOT start its own session ==')
ctl2, b2, k2 = fresh()
ctl2.on_peer_cmd('open')            # host announced the open
ctl2.on_start(FakePlayer())
assert b2.calls == [] and k2.calls == [], (b2.calls, k2.calls)
assert ctl2.active_path and not ctl2.session_host
ok('announced open -> follows, drives nobody, no loop')

print('\n== pause from the host echoes to all ==')
ctl, bed, kit = fresh()
ctl.active_path = 'plugin://x'
ctl.on_pause(FakePlayer())
assert bed.acts() == ['pause'] and kit.acts() == ['pause']
ok('host pause -> all members pause')

print('\n== pause from a NON-host echoes to all ==')
ctl, bed, kit = fresh()
ctl.active_path, ctl.session_host = 'plugin://x', False
ctl.on_pause(FakePlayer())
assert bed.acts() == ['pause'] and kit.acts() == ['pause']
ok('non-host pause -> all members pause (status irrelevant)')

print('\n== echoed pause is not re-echoed ==')
ctl, bed, kit = fresh()
ctl.active_path = 'plugin://x'
ctl.on_peer_cmd('pause')
ctl.on_pause(FakePlayer())
assert bed.calls == [] and kit.calls == []
ok('mirrored pause suppressed, no infinite echo')

print('\n== resume from any device restarts the group ==')
ctl, bed, kit = fresh()
ctl.active_path, ctl.session_host = 'plugin://x', False
bed.playing = {'path': 'plugin://x', 'time': 100.0, 'total': 5400.0, 'speed': 0}
kit.playing = dict(bed.playing)
ctl.on_resume(FakePlayer())
for b in (bed, kit):
    assert b.acts()[0].startswith('seek:900.2'), b.acts()
    assert b.acts()[1] == 'play', b.acts()
ok('non-host resume -> all seek to that device and play')

print('\n== stop/leave: others pause and are told who left ==')
ctl, bed, kit = fresh()
ctl.active_path = 'plugin://x'
ctl.on_stop()
for b in (bed, kit):
    assert b.acts() == ['pause'], b.acts()
    assert any(c.startswith('announce:left') and 'Lounge' in c for c in b.calls), b.calls
assert ctl.active_path is None
ok('stop -> all pause + "left" announcement carrying this device name')

print('\n== receiving a "left" announcement notifies the user ==')
ctl, bed, kit = fresh()
mon = service.DualMonitor(ctl)
mon.onNotification(service.ADDON_ID, 'Other.left', json.dumps({'name': 'Bedroom'}))
assert any('Bedroom left the sync group' in n for n in NOTES), NOTES
ok('"Bedroom left the sync group" shown')
SETTINGS['notify'] = False
del NOTES[:]
mon.onNotification(service.ADDON_ID, 'Other.left', json.dumps({'name': 'Attic'}))
assert any('Attic left' in n for n in NOTES), NOTES
SETTINGS['notify'] = True
ok('departure notice shown even with popups switched off')

print('\n== natural end is not a departure ==')
ctl, bed, kit = fresh()
ctl.active_path = 'plugin://x'
ctl.on_stop(ended=True)
assert bed.calls == [] and kit.calls == []
ok('end of film -> others left alone to finish')

print('\n== late join: box comes online mid-film ==')
ctl, bed, kit = fresh()
opened = []
service.local_open = lambda p: opened.append(p)
POS = {'n': 0}


def local_position():
    POS['n'] += 1
    return {'time': 0.0, 'total': 5400.0, 'speed': 1} if POS['n'] > 1 else None


service.local_position = local_position
seeks = []
service.local_seek = lambda t: seeks.append(t)
del local[:]
bed.playing = {'path': 'http://srv:8096/Videos/'
                       'a1b2c3d4e5f60718293a4b5c6d7e8f90/stream?static=true',
               'time': 1234.5, 'total': 5400.0, 'speed': 1}
ctl.try_join(FakePlayer(playing=False))
assert opened == ['plugin://plugin.video.jellyfin/?mode=play'
                  '&id=a1b2c3d4e5f60718293a4b5c6d7e8f90'], opened
assert seeks and abs(seeks[0] - 1234.8) < 0.01, seeks
assert local == [True]
assert ctl.active_path and not ctl.session_host
assert any('Joined Bedroom' in n for n in NOTES), NOTES
assert any(c.startswith('announce:joined') for c in bed.calls), bed.calls
ok('joins the running box, converts stream URL to plugin://, resumes in place')

print('\n== a paused group is not joined ==')
ctl, bed, kit = fresh()
service.local_open = lambda p: opened.append(p)
del opened[:]
bed.playing = {'path': 'plugin://x', 'time': 50.0, 'total': 5400.0, 'speed': 0}
ctl.try_join(FakePlayer(playing=False))
assert opened == [], opened
ok('everyone-paused session left alone (no auto-unpause)')

print('\n== already playing locally: no join ==')
ctl, bed, kit = fresh()
del opened[:]
bed.playing = {'path': 'plugin://x', 'time': 50.0, 'total': 5400.0, 'speed': 1}
ctl.try_join(FakePlayer(playing=True))
assert opened == []
ok('busy box does not hijack itself')

print('\n== dead box: skipped, others still served ==')
ctl, bed, kit = fresh()
ctl.active_path = 'plugin://x'
bed.down = True
ctl.on_pause(FakePlayer())
assert kit.acts() == ['pause']
assert ctl.dead.get(0, 0) > time.time()
assert any('Bedroom unreachable' in n for n in NOTES), NOTES
bed.down = False
del kit.calls[:]
ctl.on_pause(FakePlayer())
assert bed.calls == [] and kit.acts() == ['pause']
ok('one dead device does not stall or break the rest')

print('\n== leader/follower mode untouched ==')
SETTINGS['role'] = 0
SETTINGS['peer_host'] = '123.123.123.50'
ctl, bed, kit = fresh(stop_action=1)
assert not ctl.is_group
ctl.peers = [bed]
ctl.active_path = 'plugin://x'
ctl.on_stop()
assert bed.acts() == ['pause'], bed.acts()
assert not any(c.startswith('announce') for c in bed.calls), bed.calls
ok('leader mode still honours stop-action and sends no announcements')
SETTINGS['role'] = 2

print('\n%d checks passed' % len(PASS))
