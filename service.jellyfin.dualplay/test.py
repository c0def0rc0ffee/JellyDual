"""
<summary>
Settings button: check that the other box(es) are reachable.
</summary>
"""

import os
import sys

import xbmcaddon
import xbmcgui
import xbmcvfs

sys.path.insert(0, xbmcvfs.translatePath(
    os.path.join(xbmcaddon.Addon('service.jellyfin.dualplay').getAddonInfo('path'),
                 'resources', 'lib')))

from dualplay import (  # noqa: E402
    PeerError, addon, group_from_settings, local_device_name,
    peer_from_settings,
)

ROLE_GROUP = 2
HOME_WINDOW_ID = 10000
RUNNING_PROPERTY = 'JellyDualPlayTestRunning'  # on the Home window while a run is in progress


def main():
    """
    <summary>
    Refuse a second concurrent run, otherwise run the connection test with the Home window flagged for its duration.
    </summary>
    <remarks>
    Each unreachable box costs its connect timeout, so a run over several dead slots can take a while; without the flag a second press of the button would start a second run on top of the first. The flag is cleared in a finally so a failure cannot leave the button locked. This runs from the settings dialog, never during Kodi start up, so writing a Home window property here is safe.
    </remarks>
    """
    home = xbmcgui.Window(HOME_WINDOW_ID)
    dialog = xbmcgui.Dialog()
    if home.getProperty(RUNNING_PROPERTY) == 'true':
        dialog.notification('Dual Play', 'Test already running',
                            xbmcgui.NOTIFICATION_INFO, 2000, False)
        return
    home.setProperty(RUNNING_PROPERTY, 'true')
    try:
        run(dialog)
    finally:
        home.clearProperty(RUNNING_PROPERTY)


def run(dialog):
    """
    <summary>
    Ping every configured box and show the outcome in an OK dialog, listing the Control settings to check for any that failed.
    </summary>
    <param name="dialog">The xbmcgui.Dialog to show the toasts and the final OK dialog on.</param>
    <remarks>
    In Sync Group mode the device slots are tested; otherwise the single follower. A short toast names each box as it is tried, so a run over dead slots shows progress rather than nothing. Line breaks in the dialog are [CR]; a newline renders as nothing in Kodi.
    </remarks>
    """
    group_mode = addon().getSettingInt('role') == ROLE_GROUP
    if group_mode:
        peers = group_from_settings()
    else:
        p = peer_from_settings()
        peers = [p] if p.configured else []

    if not peers:
        dialog.ok('Dual Play',
                  'No devices configured.[CR][CR]'
                  'Sync Group: fill in at least one device on the Sync Group '
                  'tab.[CR]Leader: set the follower address.')
        return

    lines = []
    if group_mode:
        lines.append('This device: [B]%s[/B]' % local_device_name())
    for n, p in enumerate(peers, 1):
        dialog.notification('Dual Play', 'Testing %s (%d of %d)' % (p.label, n, len(peers)),
                            xbmcgui.NOTIFICATION_INFO, 2000, False)
        try:
            p.ping()
            state = 'playing' if (p.now_playing() or {}).get('speed') else 'idle'
            lines.append('OK: [B]%s[/B] (%s) %s:%s, %s'
                         % (p.label, p.friendly_name(), p.host, p.port, state))
        except PeerError as exc:
            lines.append('FAIL: [B]%s[/B] %s:%s, %s'
                         % (p.label, p.host, p.port, exc))

    if any(line.startswith('FAIL') for line in lines):
        lines += ['',
                  'On each unreachable box check Settings > Services > Control:',
                  '1. Allow remote control via HTTP = ON',
                  '2. Allow remote control from applications on other systems = ON']
    dialog.ok('Dual Play', '[CR]'.join(lines))


if __name__ == '__main__':
    main()
