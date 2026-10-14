"""
<summary>
Checks over service.jellyfin.dualplay/test.py, the Test connection button: the dialog text, the progress toasts and the guard against a second concurrent run.
</summary>
<remarks>
Kodi is stubbed: the dialog records what it was asked to show and the Home window keeps its properties in a dict. The peers are fakes, so nothing is contacted.
</remarks>
"""
import importlib.util
import os
import sys
import types

ADDON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'service.jellyfin.dualplay')

xbmc = types.ModuleType('xbmc')
xbmc.LOGINFO = 1; xbmc.LOGWARNING = 2; xbmc.LOGERROR = 3; xbmc.LOGDEBUG = 0
xbmc.log = lambda *a, **k: None
xbmc.getInfoLabel = lambda k: 'KodiBox'
xbmc.executeJSONRPC = lambda s: '{}'
SETTINGS = {'role': 2, 'peer_port': 8080, 'peer_user': '', 'peer_pass': '',
            'peer_host': '', 'device_name': 'Lounge'}
xbmcaddon = types.ModuleType('xbmcaddon')


class Addon(object):
    """
    <summary>
    Stand-in for xbmcaddon.Addon reading from SETTINGS.
    </summary>
    """
    def __init__(self, *a):
        """
        <summary>
        Accept and ignore the add-on id.
        </summary>
        <param name="a">Ignored.</param>
        """

    def getAddonInfo(self, k):
        """
        <summary>
        The add-on folder, whatever the key.
        </summary>
        <param name="k">Ignored.</param>
        <returns>ADDON_DIR.</returns>
        """
        return ADDON_DIR

    def getSettingInt(self, k):
        """
        <summary>
        The setting as an int.
        </summary>
        <param name="k">Setting id.</param>
        <returns>int(SETTINGS[k]).</returns>
        """
        return int(SETTINGS[k])

    def getSettingString(self, k):
        """
        <summary>
        The setting as a string.
        </summary>
        <param name="k">Setting id.</param>
        <returns>str(SETTINGS[k]).</returns>
        """
        return str(SETTINGS[k])

    def getSettingBool(self, k):
        """
        <summary>
        The setting as a bool.
        </summary>
        <param name="k">Setting id.</param>
        <returns>bool(SETTINGS[k]).</returns>
        """
        return bool(SETTINGS[k])


xbmcaddon.Addon = Addon
xbmcgui = types.ModuleType('xbmcgui')
xbmcgui.NOTIFICATION_INFO = 0
SHOWN = {'ok': [], 'toasts': []}


class Dialog(object):
    """
    <summary>
    Stand-in for xbmcgui.Dialog recording ok dialogs and toasts in SHOWN.
    </summary>
    """
    def ok(self, heading, message):
        """
        <summary>
        Record the message.
        </summary>
        <param name="heading">Ignored.</param>
        <param name="message">Appended to SHOWN['ok'].</param>
        """
        SHOWN['ok'].append(message)

    def notification(self, heading, message, *a, **k):
        """
        <summary>
        Record the toast.
        </summary>
        <param name="heading">Ignored.</param>
        <param name="message">Appended to SHOWN['toasts'].</param>
        <param name="a">Ignored.</param>
        <param name="k">Ignored.</param>
        """
        SHOWN['toasts'].append(message)


xbmcgui.Dialog = Dialog
PROPS = {}


class Window(object):
    """
    <summary>
    Stand-in for xbmcgui.Window keeping properties in PROPS.
    </summary>
    """
    def __init__(self, *a):
        """
        <summary>
        Accept and ignore the window id.
        </summary>
        <param name="a">Ignored.</param>
        """

    def getProperty(self, k):
        """
        <summary>
        The property, or empty.
        </summary>
        <param name="k">Property name.</param>
        <returns>A string.</returns>
        """
        return PROPS.get(k, '')

    def setProperty(self, k, v):
        """
        <summary>
        Store the property.
        </summary>
        <param name="k">Property name.</param>
        <param name="v">Value.</param>
        """
        PROPS[k] = v

    def clearProperty(self, k):
        """
        <summary>
        Remove the property.
        </summary>
        <param name="k">Property name.</param>
        """
        PROPS.pop(k, None)


xbmcgui.Window = Window
xbmcvfs = types.ModuleType('xbmcvfs')
xbmcvfs.translatePath = lambda p: p
for name, mod in [('xbmc', xbmc), ('xbmcaddon', xbmcaddon),
                  ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs)]:
    sys.modules[name] = mod

sys.path.insert(0, ADDON_DIR + '/resources/lib')
from dualplay import PeerError  # noqa: E402

# Loaded by path under its own name: a plain "import test" would collide with
# the standard library's test package.
spec = importlib.util.spec_from_file_location('dualplay_test_button', os.path.join(ADDON_DIR, 'test.py'))
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

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


class FakePeer(object):
    """
    <summary>
    A box the button tests: answers or fails as configured, never touches the network.
    </summary>
    """
    def __init__(self, host, name='', down=False, playing=False):
        """
        <summary>
        Set the address, the name and whether the box answers.
        </summary>
        <param name="host">Address.</param>
        <param name="name">Slot name.</param>
        <param name="down">True to fail every call.</param>
        <param name="playing">True to report speed 1 from now_playing.</param>
        """
        self.host, self.port, self.name = host, 8080, name
        self.down, self.playing = down, playing

    @property
    def configured(self):
        """
        <summary>
        Always configured.
        </summary>
        <returns>True.</returns>
        """
        return True

    @property
    def label(self):
        """
        <summary>
        The name, or the host when unnamed.
        </summary>
        <returns>A string.</returns>
        """
        return self.name or self.host

    def ping(self, timeout=None):
        """
        <summary>
        Succeed, or raise when down.
        </summary>
        <param name="timeout">Ignored.</param>
        <exception cref="PeerError">When down.</exception>
        """
        if self.down:
            raise PeerError('Cannot reach follower at %s:%s (timed out)' % (self.host, self.port))

    def now_playing(self, timeout=3):
        """
        <summary>
        A playing position when playing, else None.
        </summary>
        <param name="timeout">Ignored.</param>
        <returns>A dict or None.</returns>
        """
        return {'speed': 1} if self.playing else None

    def friendly_name(self):
        """
        <summary>
        A fixed Kodi name.
        </summary>
        <returns>'Kodi 21.0'.</returns>
        """
        return 'Kodi 21.0'


def reset():
    """
    <summary>
    Clear what the stubs recorded.
    </summary>
    """
    del SHOWN['ok'][:]
    del SHOWN['toasts'][:]
    PROPS.clear()


print('\n== dialog text ==')
reset()
script.group_from_settings = lambda: [FakePeer('123.123.123.60', 'Bedroom', playing=True),
                                      FakePeer('123.123.123.70', 'Kitchen', down=True),
                                      FakePeer('123.123.123.80')]
script.main()
assert len(SHOWN['ok']) == 1, SHOWN
text = SHOWN['ok'][0]
lines = text.split('[CR]')
assert '\n' not in text and '[CR]' in text
ok('line breaks are [CR], no newline anywhere')
assert lines[0] == 'This device: [B]Lounge[/B]'
assert lines[1].startswith('OK: [B]Bedroom[/B] (Kodi 21.0) 123.123.123.60:8080, playing'), lines[1]
assert lines[2].startswith('FAIL: [B]Kitchen[/B] 123.123.123.70:8080, Cannot reach'), lines[2]
assert lines[3].startswith('OK: [B]123.123.123.80[/B]') and lines[3].endswith('idle'), lines[3]
ok('one line per box: OK with version and state, FAIL with the error, unnamed box shown by address')
assert not any(line.startswith('- ') for line in lines), lines
assert any(line.startswith('1. Allow remote control via HTTP') for line in lines), lines
assert any(line.startswith('2. Allow remote control from applications on other systems') for line in lines), lines
ok('unreachable boxes get a numbered checklist, no hyphen bullets')
assert SHOWN['toasts'] == ['Testing Bedroom (1 of 3)', 'Testing Kitchen (2 of 3)',
                           'Testing 123.123.123.80 (3 of 3)'], SHOWN['toasts']
ok('a progress toast names each box as it is tried')
assert PROPS == {}, PROPS
ok('the running flag is cleared after the run')

print('\n== all reachable: no checklist ==')
reset()
script.group_from_settings = lambda: [FakePeer('123.123.123.60', 'Bedroom')]
script.main()
assert 'Allow remote control' not in SHOWN['ok'][0]
ok('no FAIL line, no checklist')

print('\n== nothing configured ==')
reset()
script.group_from_settings = lambda: []
script.main()
assert SHOWN['ok'][0].startswith('No devices configured.[CR][CR]')
assert SHOWN['toasts'] == []
ok('no devices: the explanatory dialog, no toasts')
reset()
SETTINGS['role'] = 0
script.peer_from_settings = lambda: FakePeer('123.123.123.50', playing=True)
script.main()
assert SHOWN['ok'][0].startswith('OK: [B]123.123.123.50[/B]') and 'This device' not in SHOWN['ok'][0]
ok('leader mode: the single follower, no "This device" line')
SETTINGS['role'] = 2

print('\n== second concurrent run is refused ==')
reset()
PROPS[script.RUNNING_PROPERTY] = 'true'
script.group_from_settings = lambda: [FakePeer('123.123.123.60', 'Bedroom')]
script.main()
assert SHOWN['ok'] == [] and SHOWN['toasts'] == ['Test already running'], SHOWN
assert PROPS[script.RUNNING_PROPERTY] == 'true'
ok('a run in progress: toast only, nothing contacted, the flag left for the owner to clear')

print('\n== the flag is cleared even when the run fails ==')
reset()


def explode():
    """
    <summary>
    Stand-in for group_from_settings that raises.
    </summary>
    <exception cref="RuntimeError">Always.</exception>
    """
    raise RuntimeError('settings exploded')


script.group_from_settings = explode
try:
    script.main()
    raise AssertionError('main swallowed the error')
except RuntimeError:
    pass
assert PROPS == {}, PROPS
ok('an exception inside the run still clears the running flag')

print('\n%d checks passed' % len(PASS))
