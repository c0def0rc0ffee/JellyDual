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
import queue
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
DEAD_SECONDS = 30        # back off after a failed command before a box is probed again
PROBE_TIMEOUT = 2        # seconds a reachability probe waits for a box marked dead
EXIT_TIMEOUT = 2         # seconds each departure command waits while Kodi exits

# Raised on the Home window by video screensavers (Dreadnought, the old
# Video Screensaver add-on) while their clip plays. Kodi has no way to mark
# a playback as a screensaver, so this property is how services tell it
# apart from a film.
HOME_WINDOW_ID = 10000
SCREENSAVER_PROPERTY = 'VideoScreensaverRunning'


def screensaver_running():
    """
    <summary>
    Whether a video screensaver has flagged its clip as playing on the Home window.
    </summary>
    <returns>True when the property reads 'true'.</returns>
    """
    return xbmcgui.Window(HOME_WINDOW_ID).getProperty(SCREENSAVER_PROPERTY) == 'true'


def peer_key(peer):
    """
    <summary>
    The key a box is tracked under in the dead map: its address and port.
    </summary>
    <param name="peer">A Peer, or anything with host and port attributes.</param>
    <returns>'host:port' as a string.</returns>
    <remarks>
    Keyed by address rather than slot index so that editing the device slots
    while a handshake is in flight cannot make a back off apply to the wrong box.
    </remarks>
    """
    return '%s:%s' % (peer.host, peer.port)


class Controller(object):
    """
    <summary>
    The state machine behind the player events: decides what each event means and drives the other boxes.
    </summary>
    <remarks>
    One instance for the service's lifetime, shared between Kodi's player callbacks, the monitor callbacks and the main loop, so the long running handshake and the join hold the lock.

    Player events are not handled on the thread that delivers them: DualPlayer posts them to a queue that one worker thread drains in order, so a slow box never blocks Kodi's callback delivery and a pause is still handled before the resume that followed it. Commands to the boxes go out in parallel, one thread per box, and a box that failed is left out of every command until a background probe from the main loop finds it answering again.
    </remarks>
    """
    def __init__(self):
        """
        <summary>
        Set up the locks, the echo suppression state, the reused monitor, the event queue and its worker, and the peer list from the settings.
        </summary>
        <remarks>
        The worker is a daemon thread, so it never keeps Kodi from exiting; on_exit stops it cleanly.
        </remarks>
        """
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
        self.expect_path = None       # the path a peer's 'open' announcement
                                      # named, so a start of something else
                                      # is still recognised as the user's
        self.left_path = None         # what this box last stopped, so an
                                      # idle box is not pulled back into a
                                      # paused film it walked out of
        self.peers = []
        self.peers_lock = threading.Lock()  # guards peers and dead together
        self.dead = {}                # peer_key -> probe-after; a box that
                                      # failed is left out of every command
                                      # until a probe finds it answering
        self.probing = False          # a probe round is running
        self.monitor = xbmc.Monitor()  # one instance, reused by the wait loops
        self.queue = queue.Queue()    # player events, drained in order
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()
        self.last_drift = time.time()
        self.last_join = time.time()
        self.reload_settings()

    # == settings =============================================================

    def reload_settings(self):
        """
        <summary>
        Re-read every setting and rebuild the peer list for the current role, keeping the back off of any box that is still configured.
        </summary>
        <remarks>
        Called at start and from DualMonitor.onSettingsChanged, which runs on another thread. The new list and the pruned dead map are swapped in together under peers_lock, and on_start snapshots the list under the same lock, so an in flight handshake always iterates one consistent list.
        </remarks>
        """
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
            peers = group_from_settings()
        elif self.role == ROLE_LEADER:
            p = peer_from_settings()
            peers = [p] if p.configured else []
        else:
            peers = []
        keep = set(peer_key(p) for p in peers)
        with self.peers_lock:
            self.peers = peers
            self.dead = dict((k, v) for k, v in self.dead.items() if k in keep)

    def _snapshot_peers(self):
        """
        <summary>
        A copy of the peer list taken under peers_lock.
        </summary>
        <returns>A new list of Peer.</returns>
        """
        with self.peers_lock:
            return list(self.peers)

    @property
    def armed(self):
        """
        <summary>
        Whether there is anything to do: enabled with at least one peer.
        </summary>
        <returns>True or False.</returns>
        """
        return self.enabled and bool(self.peers)

    # == helpers ==============================================================

    def _local_time(self, player):
        """
        <summary>
        This box's play position in seconds, 0.0 when the player cannot say.
        </summary>
        <param name="player">The xbmc.Player.</param>
        <returns>A float.</returns>
        """
        try:
            return float(player.getTime())
        except Exception:
            return 0.0

    def _ours(self, at=None):
        """
        <summary>
        True while a player event is (or echoes) our own programmatic one.
        </summary>
        <param name="at">When Kodi delivered the event, or None for now; the queue may hand an event over later than it arrived.</param>
        <returns>True when suspend is set or the event falls inside the echo window.</returns>
        """
        return self.suspend or (at or time.time()) < self.ignore_until

    def _expected(self, action, at=None):
        """
        <summary>
        True if a peer announced this command, so the event is not a user's; the announcement is used up either way.
        </summary>
        <param name="action">pause, play, seek or stop.</param>
        <param name="at">When Kodi delivered the event, or None for now.</param>
        <returns>True when an announcement for the action was pending and in date.</returns>
        <remarks>
        One announcement covers one event. Leaving it in place until it expired meant a user's genuine press of the same button within three seconds of a mirrored one was swallowed too.
        </remarks>
        """
        if not self.is_group:
            return False
        deadline = self.expect.pop(action, 0)
        return (at or time.time()) < deadline

    def on_peer_cmd(self, action, data=None):
        """
        <summary>
        A group member announced an imminent command.
        </summary>
        <param name="action">open, stop, pause, play or seek.</param>
        <param name="data">For open, a dict whose path member is what is being opened; None otherwise.</param>
        """
        if not self.is_group:
            return
        ttl = 90 if action == 'open' else 3
        self.expect[action] = time.time() + ttl
        if action == 'open':
            self.expect_path = normalize_path((data or {}).get('path') or '') or None
        log('group announced: %s' % action, xbmc.LOGDEBUG)

    def on_peer_gone(self, name):
        """
        <summary>
        A member announced that it left: tell the user, even with notifications off.
        </summary>
        <param name="name">The box's device name; may be blank.</param>
        """
        notify('%s left the sync group' % (name or 'A device'), force=True)
        log('%s left the sync group' % name)

    def on_peer_joined(self, name):
        """
        <summary>
        A member announced that it joined: tell the user.
        </summary>
        <param name="name">The box's device name; may be blank.</param>
        """
        notify('%s joined' % (name or 'A device'))
        log('%s joined the sync group' % name)

    def _consume_open(self):
        """
        <summary>
        Whether a peer's 'open' announcement is still pending, clearing it either way.
        </summary>
        <returns>A tuple: True when one was pending and in date, and the path it named or None.</returns>
        <remarks>
        Called once per on_start so a stale announcement cannot be reused.
        </remarks>
        """
        pending = time.time() < self.expect.pop('open', 0)
        path, self.expect_path = self.expect_path, None
        return pending, path

    def _alive(self, peers):
        """
        <summary>
        The peers not currently marked dead, with their keys.
        </summary>
        <param name="peers">A list of Peer.</param>
        <returns>A list of (key, peer) in slot order.</returns>
        <remarks>
        A dead box stays out until probe_dead finds it answering; the command path never waits on one.
        </remarks>
        """
        return [(peer_key(p), p) for p in peers if peer_key(p) not in self.dead]

    def _mark_dead(self, key, p, exc):
        """
        <summary>
        Record a failed box: log it, tell the user once, and keep it out of every command until a probe succeeds.
        </summary>
        <param name="key">The box's peer_key.</param>
        <param name="p">The Peer, for its label.</param>
        <param name="exc">What went wrong, for the log.</param>
        """
        log('%s: %s' % (p.label, exc), xbmc.LOGWARNING)
        if key not in self.dead:
            notify('%s unreachable' % p.label)
        self.dead[key] = time.time() + DEAD_SECONDS

    def _send(self, key, p, action, fn, data=None):
        """
        <summary>
        Announce (group mode) then run one command against one box.
        </summary>
        <param name="key">The box's peer_key, for the dead map.</param>
        <param name="p">The Peer to drive.</param>
        <param name="action">What to announce: open, stop, pause, play, seek, left or joined; blank announces nothing.</param>
        <param name="fn">Callable taking the Peer that runs the command, or None to announce only.</param>
        <param name="data">Announcement payload, or None.</param>
        <returns>True when both the announcement and the command succeeded.</returns>
        """
        try:
            if self.is_group and action:
                p.notify_cmd(action, data)
            if fn is not None:
                fn(p)
            return True
        except PeerError as exc:
            self._mark_dead(key, p, exc)
            return False

    def _fan_out(self, targets, action, fn, data=None):
        """
        <summary>
        Run one command against several boxes at the same time, one thread each, and wait for all of them.
        </summary>
        <param name="targets">A list of (key, Peer).</param>
        <param name="action">Passed to _send.</param>
        <param name="fn">Passed to _send.</param>
        <param name="data">Passed to _send.</param>
        <returns>A dict of key to Peer for the boxes the command succeeded on.</returns>
        <remarks>
        A box that times out costs its own timeout and nobody else's: the slowest box bounds the round, not the sum of them.
        </remarks>
        """
        done = {}

        def run(key, p):
            """
            <summary>
            Thread body: one _send, recording the box in done on success.
            </summary>
            <param name="key">The box's peer_key.</param>
            <param name="p">The Peer.</param>
            """
            if self._send(key, p, action, fn, data):
                done[key] = p

        threads = [threading.Thread(target=run, args=(key, p), daemon=True)
                   for key, p in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return done

    def _broadcast(self, action, fn, data=None):
        """
        <summary>
        Echo a command to every reachable member, host or not, whatever it
        is currently doing.
        </summary>
        <param name="action">Passed to _send.</param>
        <param name="fn">Passed to _send.</param>
        <param name="data">Passed to _send.</param>
        <returns>A dict of key to Peer for the boxes it reached.</returns>
        """
        return self._fan_out(self._alive(self._snapshot_peers()), action, fn, data)

    def _pause_all(self):
        """
        <summary>
        Pause every reachable member, announcing it first.
        </summary>
        """
        self._broadcast('pause', lambda p: p.set_playing(False))

    def _stop_peers(self, peers_map, action):
        """
        <summary>
        Leader / Follower mode: apply the configured stop action.
        </summary>
        <param name="peers_map">A dict of key to Peer to act on.</param>
        <param name="action">One of the STOP_ACTION constants.</param>
        """
        if action == STOP_ACTION_NONE:
            log('leaving the other box playing')
        elif action == STOP_ACTION_PAUSE:
            log('pausing the other box')
            self._fan_out(list(peers_map.items()), 'pause', lambda q: q.set_playing(False))
        else:
            self._fan_out(list(peers_map.items()), 'stop', lambda q: q.stop())

    # == dead boxes ===========================================================

    def probe_dead(self, block=False):
        """
        <summary>
        Ping every dead box whose back off has lapsed and re-admit the ones that answer.
        </summary>
        <param name="block">True runs the probe on this thread; False, the main loop's choice, runs it on a background thread so the service thread never waits on a box.</param>
        <remarks>
        One probe round at a time; a box that fails the probe gets another DEAD_SECONDS. Re-admission only ever happens here, so a powered off room is never retried on the command path.
        </remarks>
        """
        now = time.time()
        due = [key for key, when in self.dead.items() if when <= now]
        if not due or self.probing:
            return
        self.probing = True
        if block:
            self._probe(due)
        else:
            threading.Thread(target=self._probe, args=(due,), daemon=True).start()

    def _probe(self, keys):
        """
        <summary>
        The probe round itself: ping each key with PROBE_TIMEOUT and update the dead map.
        </summary>
        <param name="keys">The peer keys to probe.</param>
        """
        try:
            peers = dict((peer_key(p), p) for p in self._snapshot_peers())
            for key in keys:
                p = peers.get(key)
                if p is None:
                    self.dead.pop(key, None)   # no longer configured
                    continue
                try:
                    p.ping(timeout=PROBE_TIMEOUT)
                except PeerError as exc:
                    log('%s still unreachable: %s' % (p.label, exc), xbmc.LOGDEBUG)
                    self.dead[key] = time.time() + DEAD_SECONDS
                    continue
                self.dead.pop(key, None)
                log('%s reachable again' % p.label)
        finally:
            self.probing = False

    # == the event queue ======================================================

    def post(self, fn, *args):
        """
        <summary>
        Queue a player event handler to run on the worker thread, stamped with the time Kodi delivered it.
        </summary>
        <param name="fn">The handler, one of the on_ methods.</param>
        <param name="args">Positional arguments for it; the delivery time is added as the at keyword.</param>
        """
        self.queue.put((fn, args, time.time()))

    def _drain(self):
        """
        <summary>
        Worker thread body: run queued handlers one at a time, in arrival order, until the None sentinel.
        </summary>
        <remarks>
        A handler that raises is logged and the queue carries on; one bad event must not stop the service mirroring.
        </remarks>
        """
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                fn, args, at = item
                fn(*args, at=at)
            except Exception as exc:
                log('player event handler failed: %s' % exc, xbmc.LOGERROR)
            finally:
                self.queue.task_done()

    # == player events ========================================================

    def on_start(self, player):
        """
        <summary>
        Kodi started playing something here: follow the session another member opened on us, or host a new one.
        </summary>
        <param name="player">The DualPlayer.</param>
        <remarks>
        Hosting: pause this box, stop every reachable peer whatever it is doing, open the item on each, wait up to start_timeout for them to report a duration, pause and seek them to this box's position, start them, then release this box after lead_ms. Runs on its own thread from onAVStarted, under the lock.

        Kodi raises onAVStarted again when a stream inside the same playback is switched (the Jellyfin add-on picks the audio track just after start), so a start event for the item already in session is ignored rather than hosted a second time; hosting it again would have every box stop and reopen the others in turn.

        A screensaver clip, an unresolvable path and a stop mid-handshake all abort; the mid-handshake stop pauses the boxes already started (group) or applies the stop action to them (leader).
        </remarks>
        """
        if not self.armed or self.suspend:
            return
        if screensaver_running():
            log('video screensaver playback, not mirroring')
            return
        path = normalize_path(resolve_playable_path())
        if path and path == self.active_path and player.isPlaying():
            log('start event for the item already in session; ignoring', xbmc.LOGDEBUG)
            return
        pending, announced = self._consume_open()
        if pending and announced and path and announced != path:
            # The group opened one thing on us but the user started another
            # (or the announced open never played): this is the user's start.
            log('group announced %s but this box started %s; hosting'
                % (announced, path))
            pending = False
        if pending:
            # Another member opened this item on us, or we joined a session:
            # follow, don't host.
            with self.lock:
                self.active_path = path
                self.session_host = False
                self.left_path = None
            log('opened by the group; following')
            return
        with self.lock:
            # Snapshot the settings we need: onSettingsChanged can swap them
            # from another thread while this handshake is still running.
            peers = self._snapshot_peers()
            timeout, lead_ms = self.start_timeout, self.lead_ms
            stop_action = self.stop_action

            if not path:
                log('could not resolve a playable path; not mirroring', xbmc.LOGWARNING)
                return
            log('hosting %s' % path)
            self.left_path = None

            self.suspend = True
            try:
                # Hold this box while the others catch up.
                if not player.isPlaying():
                    return
                local_set_playing(False)

                targets = self._fan_out(self._alive(peers), 'stop', lambda q: q.stop())
                if not targets:
                    notify('No devices reachable')
                    local_set_playing(True)
                    return
                if self.monitor.waitForAbort(0.5):
                    return
                # The announcement names the path so the box can tell
                # this open from its user starting something else.
                targets = self._fan_out(list(targets.items()), 'open',
                                        lambda q: q.open_file(path),
                                        {'path': path})
                wanted = len(targets)

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
                    for key, p in list(targets.items()):
                        if key in ready:
                            continue
                        try:
                            pos = p.position()
                        except PeerError as exc:
                            # Died since it took the open: drop it rather
                            # than pay its timeout on every poll.
                            self._mark_dead(key, p, exc)
                            targets.pop(key)
                            continue
                        if pos and pos['total'] > 0:
                            ready[key] = p
                    if len(ready) == len(targets):
                        break
                    if self.monitor.waitForAbort(0.5):
                        return

                if not ready:
                    notify('No device started playback')
                    self.active_path = None
                    local_set_playing(True)  # don't strand this box paused
                    return
                if len(ready) < wanted:
                    notify('%d of %d devices started' % (len(ready), wanted))

                self._fan_out(list(ready.items()), 'pause', lambda q: q.set_playing(False))
                target_t = self._local_time(player)
                self._fan_out(list(ready.items()), 'seek', lambda q: q.seek(target_t))
                if self.monitor.waitForAbort(0.3):
                    return
                self._fan_out(list(ready.items()), 'play', lambda q: q.set_playing(True))
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

    def on_pause(self, player, at=None):
        """
        <summary>
        Kodi paused here: pause the others, unless the event is our own or one a peer announced.
        </summary>
        <param name="player">The DualPlayer.</param>
        <param name="at">When Kodi delivered the event; None means now.</param>
        """
        if not self.armed or self._ours(at) or not self.active_path:
            return
        if self._expected('pause', at):
            return
        self._pause_all()

    def on_resume(self, player, at=None):
        """
        <summary>
        Kodi resumed here: re-seek every member to just ahead of this box and start them, whoever the host is.
        </summary>
        <param name="player">The DualPlayer.</param>
        <param name="at">When Kodi delivered the event; None means now.</param>
        """
        if not self.armed or self._ours(at) or not self.active_path:
            return
        if self._expected('play', at):
            return
        # Any member may restart the group, host or not. Re-align on every
        # resume; this is what keeps loose sync honest. This box is already
        # playing again, so aim ahead of where it is right now: it advances
        # through our settle wait, and the others keep their head start on top.
        t = self._local_time(player) + 0.2 + self.lead_ms / 1000.0
        self._broadcast('seek', lambda p: p.seek(t))
        self.monitor.waitForAbort(0.2)
        self._broadcast('play', lambda p: p.set_playing(True))

    def on_seek(self, player, at=None):
        """
        <summary>
        Kodi seeked here: send the new position to every member, unless a peer announced the seek.
        </summary>
        <param name="player">The DualPlayer.</param>
        <param name="at">When Kodi delivered the event; None means now.</param>
        """
        if not self.armed or self._ours(at) or not self.active_path:
            return
        if self._expected('seek', at):
            return
        t = self._local_time(player)
        self._broadcast('seek', lambda p: p.seek(t))

    def on_stop(self, ended=False, at=None):
        """
        <summary>
        Playback stopped or ended here: leave the session and deal with the other boxes according to the mode.
        </summary>
        <param name="ended">True from onPlayBackEnded, when the item ran to its end.</param>
        <param name="at">When Kodi delivered the event; None means now.</param>
        <remarks>
        Group mode: a natural end does nothing, since the others reach their own end moments later; a stop pauses everyone and broadcasts 'left'. Leader mode: applies the stop action, except that a natural end under the pause action leaves the follower alone.
        </remarks>
        """
        was = self.active_path
        self.active_path = None
        self.session_host = False
        if was:
            self.left_path = was
        if not self.armed or self.suspend or not was:
            return
        if self._expected('stop', at):
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
        self._stop_peers(dict(self._alive(self._snapshot_peers())), self.stop_action)

    def on_exit(self):
        """
        <summary>
        Kodi is shutting down: stop the worker, then send the departure the mode calls for if a session is still open here.
        </summary>
        <remarks>
        Kodi's own OnStop for the item may still be queued when the abort lands, so the worker is given a moment to run it (which clears active_path); if the session is still open after that, the departure is sent directly with EXIT_TIMEOUT per box so shutdown is not held up by an unresponsive one. Group mode pauses the others and tells them who left; leader mode applies the stop action.
        </remarks>
        """
        self.queue.put(None)
        self.worker.join(3)
        was = self.active_path
        if not was or not self.armed:
            return
        self.active_path = None
        self.session_host = False
        peers = self._alive(self._snapshot_peers())
        for _, p in peers:
            p.timeout = EXIT_TIMEOUT
        log('exiting with a session open; sending the departure')
        if self.is_group:
            self._fan_out(peers, 'pause', lambda q: q.set_playing(False))
            self._fan_out(peers, 'left', None, {'name': self.me})
        else:
            self._stop_peers(dict(peers), self.stop_action)

    # == joining a session already in progress ================================

    def try_join(self, player):
        """
        <summary>
        Idle group member: if someone has a film open, join them where they are.
        </summary>
        <param name="player">The DualPlayer.</param>
        <remarks>
        A playing member is joined and this box plays from that position. A member that is paused is joined paused at the same point, so a box switched on after the lounge paused comes up ready and play anywhere carries the whole group on; nothing is unpaused by joining. The film this box itself stopped is not rejoined while the others sit paused in it, since the user walked out of it on purpose. A box already playing anything is never hijacked.
        </remarks>
        """
        if not (self.armed and self.is_group) or self.active_path:
            return
        if self.suspend or player.isPlaying():
            return
        if not self.lock.acquire(False):
            return
        try:
            found = None
            for key, p in self._alive(self._snapshot_peers()):
                try:
                    pos = p.now_playing()
                except PeerError as exc:
                    self._mark_dead(key, p, exc)
                    continue
                if not pos or pos['total'] <= 0:
                    continue
                path = normalize_path(pos['path'])
                if not path:
                    continue
                if not pos['speed'] and path == self.left_path:
                    # Paused in the film this box stopped: the user left it.
                    continue
                found = (p, pos, path)
                break
            if not found:
                return

            peer, pos, path = found
            log('joining %s, %s %s at %.1fs'
                % (peer.label, 'playing' if pos['speed'] else 'paused in',
                   path, pos['time']))

            self.suspend = True
            try:
                # Our own onAVStarted must follow, not start a new session.
                self.expect['open'] = time.time() + 90
                self.expect_path = path
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
                    self.expect_path = None
                    return

                # Re-read the host's position: it moved on while we loaded,
                # or was paused meanwhile.
                try:
                    fresh = peer.now_playing() or pos
                except PeerError:
                    fresh = pos
                playing = bool(fresh['speed'])
                target = fresh['time'] + (0.3 if playing else 0.0)
                local_seek(target)
                self.monitor.waitForAbort(0.2)
                # A paused group stays paused: play anywhere carries it on.
                local_set_playing(playing)
                self.active_path = path
                self.session_host = False
                self.left_path = None
            finally:
                # Our own onAVStarted was blocked by suspend, so the announced
                # open above was never consumed. Clear it here, or a restart
                # of the same film within 90 s would follow instead of host.
                self.expect.pop('open', None)
                self.expect_path = None
                self.ignore_until = time.time() + 1.0
                self.suspend = False

            notify('Joined %s%s' % (peer.label, '' if playing else ' (paused)'))
            self._broadcast('joined', None, {'name': self.me})
        finally:
            self.lock.release()

    # == drift correction =====================================================

    def check_drift(self, player):
        """
        <summary>
        Host only: compare each playing member's position with this box's and re-seek any that drifted beyond the tolerance.
        </summary>
        <param name="player">The DualPlayer.</param>
        <remarks>
        Skipped rather than queued when the handshake holds the lock.
        </remarks>
        """
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
            for key, p in self._alive(self._snapshot_peers()):
                try:
                    pos = p.position()
                except PeerError as exc:
                    self._mark_dead(key, p, exc)
                    continue
                if not pos or pos['speed'] == 0:
                    continue
                delta = local - pos['time']
                if abs(delta) > self.drift_tolerance:
                    log('drift %.1fs on %s, re-seeking' % (delta, p.label))
                    self._send(key, p, 'seek', lambda q: q.seek(local))
        finally:
            self.lock.release()

    # == the main loop's work =================================================

    def tick(self, player):
        """
        <summary>
        One pass of the main loop: probe dead boxes, run the drift check and the join poll when their intervals are due.
        </summary>
        <param name="player">The DualPlayer.</param>
        <remarks>
        Both the drift check and the join attempt are wrapped so one exception is logged and the service carries on; an unguarded drift check once ended the loop for the rest of the Kodi session.
        </remarks>
        """
        now = time.time()
        self.probe_dead()
        if self.drift_check and now - self.last_drift >= self.drift_interval:
            self.last_drift = now
            try:
                self.check_drift(player)
            except Exception as exc:  # never let the loop die on a drift check
                log('drift check failed: %s' % exc, xbmc.LOGWARNING)
        if self.is_group and now - self.last_join >= JOIN_POLL_SECONDS:
            self.last_join = now
            try:
                self.try_join(player)
            except Exception as exc:  # never let the loop die on a join attempt
                log('join attempt failed: %s' % exc, xbmc.LOGWARNING)


class DualPlayer(xbmc.Player):
    """
    <summary>
    Kodi player callbacks forwarded to the Controller.
    </summary>
    <remarks>
    onAVStarted hands off to a thread because the handshake blocks for up to the start timeout and Kodi's callbacks must return promptly. Every other event is posted to the controller's queue for the same reason: the handler talks to the other boxes, and a box that is slow to answer must not hold up the thread Kodi delivers events on. One worker drains the queue, so the order Kodi raised the events in is kept.
    </remarks>
    """
    def __init__(self, controller):
        """
        <summary>
        Keep the controller.
        </summary>
        <param name="controller">The shared Controller.</param>
        """
        super(DualPlayer, self).__init__()
        self.ctl = controller

    def onAVStarted(self):
        """
        <summary>
        Kodi hook: audio or video is now playing; run on_start on a daemon thread.
        </summary>
        """
        threading.Thread(target=self.ctl.on_start, args=(self,), daemon=True).start()

    def onPlayBackPaused(self):
        """
        <summary>
        Kodi hook: queued for on_pause.
        </summary>
        """
        self.ctl.post(self.ctl.on_pause, self)

    def onPlayBackResumed(self):
        """
        <summary>
        Kodi hook: queued for on_resume.
        </summary>
        """
        self.ctl.post(self.ctl.on_resume, self)

    def onPlayBackSeek(self, seek_time, seek_offset):
        """
        <summary>
        Kodi hook: queued for on_seek; the arguments are unused because the position is read back from the player.
        </summary>
        <param name="seek_time">Unused.</param>
        <param name="seek_offset">Unused.</param>
        """
        self.ctl.post(self.ctl.on_seek, self)

    def onPlayBackSeekChapter(self, chapter):
        """
        <summary>
        Kodi hook: a chapter jump is treated as a seek and queued for on_seek.
        </summary>
        <param name="chapter">Unused.</param>
        """
        self.ctl.post(self.ctl.on_seek, self)

    def onPlayBackStopped(self):
        """
        <summary>
        Kodi hook: queued for on_stop as a user stop.
        </summary>
        """
        self.ctl.post(self.ctl.on_stop, False)

    def onPlayBackEnded(self):
        """
        <summary>
        Kodi hook: queued for on_stop as a natural end.
        </summary>
        """
        self.ctl.post(self.ctl.on_stop, True)

    def onPlayBackError(self):
        """
        <summary>
        Kodi hook: a playback failure is treated as a stop and queued for on_stop.
        </summary>
        """
        self.ctl.post(self.ctl.on_stop, False)


class DualMonitor(xbmc.Monitor):
    """
    <summary>
    Kodi monitor callbacks: settings changes and the announcements members send each other.
    </summary>
    """
    def __init__(self, controller):
        """
        <summary>
        Keep the controller.
        </summary>
        <param name="controller">The shared Controller.</param>
        """
        super(DualMonitor, self).__init__()
        self.ctl = controller

    def onSettingsChanged(self):
        """
        <summary>
        Kodi hook: reload the settings and log the new role and group.
        </summary>
        """
        self.ctl.reload_settings()
        log('settings reloaded (role=%s, group=%s)'
            % (self.ctl.role, [p.label for p in self.ctl.peers] or 'empty'))

    def onNotification(self, sender, method, data):
        # Members announce their commands as addon notifications; the message
        # arrives with the sender we set and method 'Other.<action>'.
        """
        <summary>
        Kodi hook: turn an announcement from another member into on_peer_gone, on_peer_joined or on_peer_cmd.
        </summary>
        <param name="sender">The announcing add-on; anything but this add-on's id is ignored.</param>
        <param name="method">'Other.<action>' as Kodi delivers a NotifyAll message.</param>
        <param name="data">JSON text; for left and joined, a dict carrying the sender's device name; for open, a dict carrying the path being opened.</param>
        """
        if sender != ADDON_ID:
            return
        action = method.split('.', 1)[-1]
        payload = None
        if data:
            try:
                payload = json.loads(data)
            except Exception:
                payload = None
        if not isinstance(payload, dict):
            payload = None
        if action in ('left', 'joined'):
            name = (payload or {}).get('name', '')
            if action == 'left':
                self.ctl.on_peer_gone(name)
            else:
                self.ctl.on_peer_joined(name)
            return
        self.ctl.on_peer_cmd(action, payload)


def main():
    """
    <summary>
    Service entry point: build the controller, player and monitor, then loop until Kodi exits, handing each pass to Controller.tick; on exit, send the departure for any session still open.
    </summary>
    <remarks>
    The loop body lives in Controller.tick so it can be tested; tick guards the drift check and the join attempt so neither can end the loop.
    </remarks>
    """
    controller = Controller()
    player = DualPlayer(controller)
    monitor = DualMonitor(controller)
    log('service started as "%s" (role=%s, group=%s)'
        % (controller.me, controller.role,
           [p.label for p in controller.peers] or 'empty'))

    while not monitor.abortRequested():
        if monitor.waitForAbort(2):
            break
        controller.tick(player)

    controller.on_exit()
    log('service stopped')
    del player
    del monitor


if __name__ == '__main__':
    main()
