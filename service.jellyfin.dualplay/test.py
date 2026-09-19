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


def main():
    """
    <summary>
    Ping every configured box and show the outcome in an OK dialog, listing the Control settings to check for any that failed.
    </summary>
    <remarks>
    In Sync Group mode the device slots are tested; otherwise the single follower.
    </remarks>
    """
    group_mode = addon().getSettingInt('role') == ROLE_GROUP
    if group_mode:
        peers = group_from_settings()
    else:
        p = peer_from_settings()
        peers = [p] if p.configured else []

    dialog = xbmcgui.Dialog()
    if not peers:
        dialog.ok('Dual Play',
                  'No devices configured.[CR][CR]'
                  'Sync Group: fill in at least one device on the Sync Group '
                  'tab.[CR]Leader: set the follower address.')
        return

    # Kodi dialogs want [CR] for line breaks, not \n.
    lines = []
    if group_mode:
        lines.append('This device: [B]%s[/B]' % local_device_name())
    for p in peers:
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
                  '- Allow remote control via HTTP = ON',
                  '- Allow remote control from other systems = ON']
    dialog.ok('Dual Play', '[CR]'.join(lines))


if __name__ == '__main__':
    main()
