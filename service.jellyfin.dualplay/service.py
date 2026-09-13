"""
<summary>
Jellyfin Dual Play: Kodi service entry point.
</summary>
<remarks>
Two ways to run:

Leader / Follower: two boxes, one-way. The leader mirrors play / pause / seek
/ stop onto the follower; the follower's own addon does nothing.

Sync Group: any number of boxes (up to 10 others each), all equal. Whichever
box starts something becomes that session's HOST and opens the item on every
other member; the others never start a session of their own. After that,
pause and resume from ANY member are echoed to every member regardless of
which one is host. When a member stops or exits, everyone else pauses and is
told which box left. A box that comes online mid-session joins by itself and
picks the item up at the right position.

The loop problem: a mirrored command lands on a box as a perfectly ordinary
player event, indistinguishable from the user pressing the button, so the box
would mirror it onwards forever. To break the loop, every command is announced
via JSONRPC.NotifyAll before it is sent; the receiving addon hears the
announcement (Monitor.onNotification) and knows the next matching event is not
a user action.
</remarks>
"""

import json
import os
import sys
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

sys.path.insert(0, xbmcvfs.translatePath(
    os.path.join(xbmcaddon.Addon('service.jellyfin.dualplay').getAddonInfo('path'),
                 'resources', 'lib')))

from dualplay import (  # noqa: E402
    ADDON_ID, PeerError, addon, group_from_settings, local_device_name,
    local_open, local_position, local_seek, local_set_playing, log, notify,
    normalize_path, peer_from_settings, resolve_playable_path,
)

ROLE_LEADER = 0
ROLE_FOLLOWER = 1
ROLE_GROUP = 2

# What happens to the other boxes when this one stops or exits.
# Sync Group always pauses them (and says who left), so this only applies to
# Leader / Follower mode.
STOP_ACTION_STOP = 0
STOP_ACTION_PAUSE = 1
STOP_ACTION_NONE = 2

JOIN_POLL_SECONDS = 10   # how often an idle group member looks for a session

# Raised on the Home window by video screensavers (Dreadnought, the old
# Video Screensaver add-on) while their clip plays. Kodi has no way to mark
# a playback as a screensaver, so this property is how services tell it
# apart from a film.
HOME_WINDOW_ID = 10000
SCREENSAVER_PROPERTY = 'VideoScreensaverRunning'


def screensaver_running():
    return xbmcgui.Window(HOME_WINDOW_ID).getProperty(SCREENSAVER_PROPERTY) == 'true'


class Controller(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.suspend = False          # ignore our own programmatic player events
        self.ignore_until = 0.0       # ...and their echoes: suspend is cleared
                                      # before Kodi delivers the resumed event
                                      # for our own final PlayPause, so events
                                      # inside this window are still ours
        self.active_path = None       # path currently shared with the group
        self.session_host = False     # this box ran the start-up handshake
        self.expect = {}              # action -> deadline; commands announced
                                      # by a peer, so the matching player event
                                      # is not a user action
        self.dead = {}                # peer index -> retry-after; unreachable
                                      # boxes are skipped for a while so every
                                      # button press doesn't stall on timeouts
        self.monitor = xbmc.Monitor()  # one instance, reused by the wait loops
        self.reload_settings()

    # -- settings -------------------------------------------------------------

    def reload_settings(self):
        a = addon()
        self.enabled = a.getSettingBool('enabled')
        self.role = a.getSettingInt('role')
        self.is_group = self.role == ROLE_GROUP
        self.start_timeout = a.getSettingInt('start_timeout')
        self.lead_ms = a.getSettingInt('lead_ms')
        self.drift_check = a.getSettingBool('drift_check')
        self.drift_interval = a.getSettingInt('drift_interval')
        self.drift_tolerance = a.getSettingInt('drift_tolerance')
        self.stop_action = a.getSettingInt('stop_action')
        self.me = local_device_name()
        if self.is_group:
            self.peers = group_from_settings()
        elif self.role == ROLE_LEADER:
            p = peer_from_settings()
            self.peers = [p] if p.configured else []
        else:
            self.peers = []
        self.dead = {}

    @property
    def armed(self):
        return self.enabled and bool(self.peers)

    # -- helpers --------------------------------------------------------------

    def _local_time(self, player):
        try:
            return float(player.getTime())
        except Exception:
            return 0.0

    def _ours(self):
        """True while a player event is (or echoes) our own programmatic one."""
        return self.suspend or time.time() < self.ignore_until

    def _expected(self, action):
        """True if a peer announced this command, so the event is not a user's."""
        return self.is_group and time.time() < self.expect.get(action, 0)

    def on_peer_cmd(self, action):
        """A group member announced an imminent command."""
        if not self.is_group:
            return
        ttl = 90 if action == 'open' else 3
        self.expect[action] = time.time() + ttl
        log('group announced: %s' % action, xbmc.LOGDEBUG)

    def on_peer_gone(self, name):
        notify('%s left the sync group' % (name or 'A device'), force=True)
        log('%s left the sync group' % name)

    def on_peer_joined(self, name):
        notify('%s joined' % (name or 'A device'))
        log('%s joined the sync group' % name)

    def _consume_open(self):
        return time.time() < self.expect.pop('open', 0)

    def _alive(self, peers):
        now = time.time()
        return [(i, p) for i, p in enumerate(peers) if self.dead.get(i, 0) <= now]

    def _send(self, i, p, action, fn, data=None):
        """Announce (group mode) then run one command against one box."""
        try:
            if self.is_group and action:
                p.notify_cmd(action, data)
            if fn is not None:
                fn(p)
            return True
        except PeerError as exc:
            log('%s: %s' % (p.label, exc), xbmc.LOGWARNING)
            if self.dead.get(i, 0) <= time.time():
                notify('%s unreachable' % p.label)
            self.dead[i] = time.time() + 30
            return False

    def _broadcast(self, action, fn, data=None):
        """Echo a command to every reachable member, host or not, whatever it
        is currently doing."""
        for i, p in self._alive(self.peers):
            self._send(i, p, action, fn, data)

    def _pause_all(self):
        self._broadcast('pause', lambda p: p.set_playing(False))

    def _stop_peers(self, peers_map, action):
        """Leader / Follower mode: apply the configured stop action."""
        if action == STOP_ACTION_NONE:
            log('leaving the other box playing')
        elif action == STOP_ACTION_PAUSE:
            log('pausing the other box')
            for i, p in peers_map.items():
                self._send(i, p, 'pause', lambda q: q.set_playing(False))
        else:
            for i, p in peers_map.items():
                self._send(i, p, 'stop', lambda q: q.stop())

    # -- player events --------------------------------------------------------

    def on_start(self, player):
        if not self.armed or self.suspend:
            return
        if screensaver_running():
            log('video screensaver playback, not mirroring')
            return
        if self._consume_open():
            # Another member opened this item on us, or we joined a session:
            # follow, don't host.
            with self.lock:
                self.active_path = resolve_playable_path()
                self.session_host = False
            log('opened by the group; following')
            return
        with self.lock:
            # Snapshot the settings we need: onSettingsChanged can swap them
            # from another thread while this handshake is still running.
            peers = list(self.peers)
            timeout, lead_ms = self.start_timeout, self.lead_ms
            stop_action = self.stop_action

            path = resolve_playable_path()
            if not path:
                log('could not resolve a playable path; not mirroring', xbmc.LOGWARNING)
                return
            log('hosting %s' % path)

            self.suspend = True
            try:
                # Hold this box while the others catch up.
                if not player.isPlaying():
                    return
                local_set_playing(False)

                targets = {}
                for i, p in self._alive(peers):
                    if self._send(i, p, 'stop', lambda q: q.stop()):
                        targets[i] = p
                if not targets:
                    notify('No devices reachable')
                    local_set_playing(True)
                    return
                if self.monitor.waitForAbort(0.5):
                    return
                for i in list(targets):
                    if not self._send(i, targets[i], 'open',
                                      lambda q: q.open_file(path)):
                        targets.pop(i)

                deadline = time.time() + timeout
                ready = {}
                while time.time() < deadline and len(ready) < len(targets):
                    # User stopped this box mid-handshake: don't leave the
                    # others playing on their own.
                    if not player.isPlaying():
                        log('host stopped during handshake')
                        if self.is_group:
                            self._pause_all()
                        else:
                            self._stop_peers(targets, stop_action)
                        return
                    for i, p in list(targets.items()):
                        if i in ready:
                            continue
                        try:
                            pos = p.position()
                        except PeerError:
                            continue
                        if pos and pos['total'] > 0:
                            ready[i] = p
                    if len(ready) == len(targets):
                        break
                    if self.monitor.waitForAbort(0.5):
                        return

                if not ready:
                    notify('No device started playback')
                    self.active_path = None
                    local_set_playing(True)  # don't strand this box paused
                    return
                if len(ready) < len(targets):
                    notify('%d of %d devices started' % (len(ready), len(targets)))

                for i, p in ready.items():
                    self._send(i, p, 'pause', lambda q: q.set_playing(False))
                target_t = self._local_time(player)
                for i, p in ready.items():
                    self._send(i, p, 'seek', lambda q: q.seek(target_t))
                if self.monitor.waitForAbort(0.3):
                    return
                for i, p in ready.items():
                    self._send(i, p, 'play', lambda q: q.set_playing(True))
                self.active_path = path
                self.session_host = True

                # Give the others a small head start, then release this box.
                if lead_ms and self.monitor.waitForAbort(lead_ms / 1000.0):
                    return
                if not player.isPlayingVideo() and not player.isPlayingAudio():
                    # This box went away while we were setting up.
                    log('host gone before release')
                    self.active_path = None
                    if self.is_group:
                        self._pause_all()
                    else:
                        self._stop_peers(ready, stop_action)
                    return
                local_set_playing(True)
                notify('Playing on %d screens' % (len(ready) + 1))
            finally:
                # Our own final PlayPause echoes back as onPlayBackResumed
                # *after* this block runs; give the echo a moment to land.
                self.ignore_until = time.time() + 1.0
                self.suspend = False

    def on_pause(self, player):
        if not self.armed or self._ours() or not self.active_path:
            return
        if self._expected('pause'):
            return
        self._pause_all()

    def on_resume(self, player):
        if not self.armed or self._ours() or not self.active_path:
            return
        if self._expected('play'):
            return
        # Any member may restart the group, host or not. Re-align on every
        # resume; this is what keeps loose sync honest. This box is already
        # playing again, so aim ahead of where it is right now: it advances
        # through our settle wait, and the others keep their head start on top.
        t = self._local_time(player) + 0.2 + self.lead_ms / 1000.0
        self._broadcast('seek', lambda p: p.seek(t))
        self.monitor.waitForAbort(0.2)
        self._broadcast('play', lambda p: p.set_playing(True))

    def on_seek(self, player):
        if not self.armed or self._ours() or not self.active_path:
            return
        if self._expected('seek'):
            return
        t = self._local_time(player)
        self._broadcast('seek', lambda p: p.seek(t))

    def on_stop(self, ended=False):
        was = self.active_path
        self.active_path = None
        self.session_host = False
        if not self.armed or self.suspend or not was:
            return
        if self._expected('stop'):
            return
        if self.is_group:
            # Leaving the group: everyone else holds their place, and is told
            # who dropped out. A natural end is not a departure, so the others
            # reach their own end moments later.
            if ended:
                return
            self._pause_all()
            self._broadcast('left', None, {'name': self.me})
            return
        if ended and self.stop_action == STOP_ACTION_PAUSE:
            return
        self._stop_peers(dict(self._alive(self.peers)), self.stop_action)

    # -- joining a session already in progress --------------------------------

    def try_join(self, player):
        """Idle group member: if someone is playing, join them where they are."""
        if not (self.armed and self.is_group) or self.active_path:
            return
        if self.suspend or player.isPlaying():
            return
        if not self.lock.acquire(False):
            return
        try:
            found = None
            for i, p in self._alive(self.peers):
                try:
                    pos = p.now_playing()
                except PeerError as exc:
                    log('%s: %s' % (p.label, exc), xbmc.LOGDEBUG)
                    self.dead[i] = time.time() + 30
                    continue
                # Only join something actually running: a group that everyone
                # has paused should stay paused.
                if pos and pos['speed'] and pos['total'] > 0:
                    found = (p, pos)
                    break
            if not found:
                return

            peer, pos = found
            path = normalize_path(pos['path'])
            if not path:
                return
            log('joining %s already playing %s at %.1fs'
                % (peer.label, path, pos['time']))

            self.suspend = True
            try:
                # Our own onAVStarted must follow, not start a new session.
                self.expect['open'] = time.time() + 90
                local_open(path)

                deadline = time.time() + self.start_timeout
                ready = False
                while time.time() < deadline:
                    local = local_position()
                    if local and local['total'] > 0:
                        ready = True
                        break
                    if self.monitor.waitForAbort(0.5):
                        return
                if not ready:
                    log('join failed: playback did not start here', xbmc.LOGWARNING)
                    self.expect.pop('open', None)
                    return

                # Re-read the host's position: it moved on while we loaded.
                try:
                    fresh = peer.now_playing()
                except PeerError:
                    fresh = None
                target = (fresh or pos)['time'] + 0.3
                local_seek(target)
                self.monitor.waitForAbort(0.2)
                local_set_playing(True)
                self.active_path = path
                self.session_host = False
            finally:
                self.ignore_until = time.time() + 1.0
                self.suspend = False

            notify('Joined %s' % peer.label)
            self._broadcast('joined', None, {'name': self.me})
        finally:
            self.lock.release()

    # -- drift correction -----------------------------------------------------

    def check_drift(self, player):
        if not (self.armed and self.drift_check and self.active_path
                and self.session_host):
            return
        if self.suspend or not player.isPlaying():
            return
        # Don't queue up behind a start-up handshake, because it can hold the lock for
        # the whole start timeout, and by the time it finishes it has re-synced
        # anyway. Skip this round instead.
        if not self.lock.acquire(False):
            return
        try:
            if self.suspend or not self.active_path:
                return
            local = self._local_time(player)
            for i, p in self._alive(self.peers):
                try:
                    pos = p.position()
                except PeerError:
                    continue
                if not pos or pos['speed'] == 0:
                    continue
                delta = local - pos['time']
                if abs(delta) > self.drift_tolerance:
                    log('drift %.1fs on %s, re-seeking' % (delta, p.label))
                    self._send(i, p, 'seek', lambda q: q.seek(local))
        finally:
            self.lock.release()


class DualPlayer(xbmc.Player):
    def __init__(self, controller):
        super(DualPlayer, self).__init__()
        self.ctl = controller

    def onAVStarted(self):
        threading.Thread(target=self.ctl.on_start, args=(self,), daemon=True).start()

    def onPlayBackPaused(self):
        self.ctl.on_pause(self)

    def onPlayBackResumed(self):
        self.ctl.on_resume(self)

    def onPlayBackSeek(self, seek_time, seek_offset):
        self.ctl.on_seek(self)

    def onPlayBackSeekChapter(self, chapter):
        self.ctl.on_seek(self)

    def onPlayBackStopped(self):
        self.ctl.on_stop()

    def onPlayBackEnded(self):
        self.ctl.on_stop(ended=True)

    def onPlayBackError(self):
        self.ctl.on_stop()


class DualMonitor(xbmc.Monitor):
    def __init__(self, controller):
        super(DualMonitor, self).__init__()
        self.ctl = controller

    def onSettingsChanged(self):
        self.ctl.reload_settings()
        log('settings reloaded (role=%s, group=%s)'
            % (self.ctl.role, [p.label for p in self.ctl.peers] or 'empty'))

    def onNotification(self, sender, method, data):
        # Members announce their commands as addon notifications; the message
        # arrives with the sender we set and method 'Other.<action>'.
        if sender != ADDON_ID:
            return
        action = method.split('.', 1)[-1]
        if action in ('left', 'joined'):
            name = ''
            try:
                name = (json.loads(data) or {}).get('name', '')
            except Exception:
                pass
            if action == 'left':
                self.ctl.on_peer_gone(name)
            else:
                self.ctl.on_peer_joined(name)
            return
        self.ctl.on_peer_cmd(action)


def main():
    controller = Controller()
    player = DualPlayer(controller)
    monitor = DualMonitor(controller)
    log('service started as "%s" (role=%s, group=%s)'
        % (controller.me, controller.role,
           [p.label for p in controller.peers] or 'empty'))

    last_drift = time.time()
    last_join = time.time()
    while not monitor.abortRequested():
        if monitor.waitForAbort(2):
            break
        now = time.time()
        if controller.drift_check and now - last_drift >= controller.drift_interval:
            last_drift = now
            controller.check_drift(player)
        if controller.is_group and now - last_join >= JOIN_POLL_SECONDS:
            last_join = now
            try:
                controller.try_join(player)
            except Exception as exc:  # never let the loop die on a join attempt
                log('join attempt failed: %s' % exc, xbmc.LOGWARNING)

    log('service stopped')
    del player
    del monitor


if __name__ == '__main__':
    main()
