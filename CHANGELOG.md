# Changelog

## 2.1.1

- Ignore video screensaver playback (`VideoScreensaverRunning` flag); group
  members no longer try to join a host whose screensaver is running.

## 2.1.0

Peer mode becomes **Sync Group**, on its own settings tab:

- **10 device slots**, each with its own name and address, instead of one
  comma-separated field. A name (e.g. "Bedroom") is what other boxes show in
  notifications; blank falls back to the address. Slots may be filled in any
  order, gaps are skipped. `:port` on a slot overrides the shared port.
- **This device's name** setting; blank uses Kodi's own device name.
- **Host**: whichever device starts something hosts it and opens it on all the
  others; a device that was opened by the host never starts a session of its
  own.
- **Pause and resume echo from any device**, host or not, whatever the other
  devices are currently doing. Resume from any device re-aligns the whole
  group to that device, so pause in the lounge, walk to the bedroom, press
  play there and everyone follows.
- **Stop or exit on any device pauses all the others** and shows them
  "<name> left the sync group". Departure notices appear even with popups
  switched off. A natural end-of-film is not a departure.
- **Late join**: an idle member checks every 10 seconds for a device that's
  actually playing, then opens the same item and picks it up at that position
  (converting a Jellyfin stream URL to a plugin:// path so it uses its own
  session). A group that everyone has paused is left paused, and a device
  already playing something is never hijacked.
- "When this box stops" now applies to Leader/Follower mode only, the sync
  group always pauses and reports the departure.
- Added `tests/test_syncgroup.py`: 17 checks over the group state machine
  against stubbed Kodi boxes (`python3 tests/test_syncgroup.py`).

## 2.0.0

- **Peer mode**: set every box's role to *Peer (any box can start)* and give
  each one the addresses of all the others. Whichever box you start playback
  on becomes that session's leader and opens the item everywhere; after that,
  pause, resume and seek from **any** box mirror to all of them, and **When
  this box stops** applies from any box too (set it to *Pause the others* to
  stop in one room and press play in another).
- Mirrored commands are announced over `JSONRPC.NotifyAll` a moment before
  they land, so each box can tell a peer's command from its user's own button
  press, otherwise every mirrored command would be re-mirrored forever.
- Unreachable peers are skipped for 30 seconds after a failure instead of
  stalling every button press on a timeout, and one dead box no longer
  aborts the session for the rest.
- Start-up waits for all peers and starts everyone who made it, reporting
  "N of M peers started" if some didn't.
- Natural end-of-file no longer pauses the other boxes moments before their
  own credits (they finish on their own).
- Stop-action option labels reworded for multiple boxes ("Pause the others"
  etc.). Leader/Follower mode behaves exactly as in 1.2.0.

## 1.2.0

- The 1.1.0 stop toggle is now a three-way option, **When this box stops**
  (leader, Dual Play tab): *Stop the follower* (default), *Pause the
  follower*, or *Leave it playing*. Pause is the move-rooms option: start a
  film in the lounge, exit, walk to the bedroom and press play to carry on
  from the same spot. (Replaces the `mirror_stop` setting; if you'd flipped
  that off, re-pick your choice here.)

## 1.1.0

- New option **Stop follower on stop/exit** (leader, Dual Play tab, default
  ON = old behaviour). Turned off, stopping playback or shutting down the
  leader leaves the follower playing to the end; play, pause, resume and
  seek are still mirrored. Applies to mid-handshake aborts too.

## 1.0.2

Settings dialog fixes, labels were truncated in Kodi's settings list:

- All labels shortened to fit ("Seconds to wait for follower to start" →
  "Start timeout (seconds)", "Follower IP address or hostname" → "Follower
  address", etc.); the full explanations moved to per-setting help text,
  which Kodi shows full-width at the bottom of the dialog.
- Added a proper `strings.po` (en_GB), all settings labels and help now go
  through string ids, as the modern settings format expects. The old inline
  `<help>` text was silently ignored by Kodi for exactly this reason.

## 1.0.1

Reliability fixes, all in the leader's start-up handshake and event handling:

- The leader used `xbmc.Player().pause()`, a toggle, to resume itself after
  the handshake. Pausing while the follower was loading made that "resume"
  pause instead, leaving both boxes stopped. Play/pause on the leader is now
  an explicit `Player.PlayPause` with a play flag.
- Stopping the leader mid-handshake left the follower playing on its own
  forever. The handshake now watches for the leader disappearing and stops
  the follower.
- The leader's own post-handshake resume echoed back as a player event after
  the suspend flag was cleared, and the resulting "user resume" re-seek wiped
  out the follower's head start. Programmatic events now have a short grace
  window in which echoes are ignored.
- Every user pause/resume dragged the follower about 0.2 s behind: the
  re-align seek targeted the leader's position at the moment of the event,
  but the leader kept playing through the settle wait. The seek now aims
  ahead by the settle time plus the configured head start.
- A failed handshake ("Follower did not start playback") left the leader
  paused with no way back except a manual unpause. It now resumes itself.
- Opening the settings dialog mid-handshake could swap the follower address
  and timing values underneath it. They are snapshotted when the handshake
  starts.
- The wait loops built a fresh `xbmc.Monitor()` per iteration and slept with
  `time.sleep()`, ignoring Kodi shutdown for up to half a second at a time.
  One monitor is reused and every wait is a `waitForAbort`.
- The periodic drift check could fire mid-handshake and seek the follower to
  a stale position. It now skips the round if the handshake holds the lock.
- A follower address pasted with a path (`123.123.123.50:8080/jsonrpc`) broke
  the port parsing and produced a confusing connection error. Any path is now
  stripped.
- The test-connection dialog used `\n` for line breaks, which many Kodi skins
  render as nothing. It now uses `[CR]`.

## 1.0.0

Initial release.

Leader/follower synchronised playback for two Kodi boxes sharing a Jellyfin
server. The leader forwards play, pause, seek and stop to the follower over
Kodi's JSON-RPC API; each box streams from Jellyfin in its own session.
