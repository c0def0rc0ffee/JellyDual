# Jellyfin Dual Play

Plays the same thing on two or more Kodi boxes at once, in sync, when all use
the same Jellyfin server through the Jellyfin for Kodi addon.

Two modes:

- **Leader / Follower**: two boxes, one-way control from the leader.
- **Sync Group**: up to eleven boxes, each with its own name. Whichever box
  you start a film on hosts it and opens it everywhere; pause and resume from
  **any** box are echoed to all of them. Stopping or exiting anywhere pauses
  everyone else and tells them which device left, and a box switched on
  mid-film joins by itself at the right position. Commands are announced over
  `JSONRPC.NotifyAll` before they land so each box can tell a group command
  from its own user's button press (that's what stops mirrored commands being
  re-mirrored forever).

Jellyfin's own **SyncPlay** does not work in the Kodi addon, only the web
client, Jellyfin Media Player and mpv-shim support it. This addon does the job a
different way: it drives the second box over Kodi's built-in JSON-RPC API.

## How it works

One box is the **leader**, the other the **follower**. When you start something
on the leader, it:

1. Works out a path the follower can open, a `plugin://plugin.video.jellyfin/…`
   URL, so the follower streams from Jellyfin using its *own* session rather than
   piggy-backing on the leader's.
2. Pauses itself, tells the follower to open that item, and waits for it to load.
3. Seeks the follower to the leader's position, starts it, gives it a small head
   start, then releases itself.

After that, pause, resume, seek and stop on the leader are forwarded to the
follower. Every resume also re-seeks the follower, which quietly cleans up any
drift that crept in.

## Install

> Full walk-through with troubleshooting: see **SETUP.md** in the project
> folder.

Install the zip on **both** boxes: *Settings → Add-ons → Install from zip file*.
(You may need *Unknown sources* enabled first.) Or install the [DeliciousCoffee repository](https://github.com/c0def0rc0ffee/DeliciousCoffee) once and Kodi installs and updates it for you.

### On the follower

1. *Settings → Services → Control*
   - **Allow remote control via HTTP** → ON (note the port, default 8080)
   - **Allow remote control from applications on other systems** → ON
   - Set a username/password, or clear both if you'd rather not use one.
2. *Settings → Add-ons → My add-ons → Services → Jellyfin Dual Play → Configure*
   - **This box is the** → Follower

That's all the follower needs, the addon itself does nothing there. It's
installed so you can swap the roles later without reinstalling.

### On the leader

*Configure* the addon and set:

- **This box is the** → Leader
- **Follower address** → e.g. `123.123.123.50` (a `123.123.123.50:8080` string
  works too, the port gets split out)
- **Port** → matches the follower's setting
- **Username / Password** → matches the follower's setting, or leave
  both blank if you disabled auth

Hit **Test connection**. It should report the follower's Kodi
version.

### Sync Group

For three or more boxes, or to control playback from any of them: on **every**
box set **This box is the** to the Sync Group member option, give the box a
**Device name**, switch on HTTP remote control exactly as for the follower
above, and list every other box on the **Sync Group** tab (up to ten devices,
each a name and an address). Any box can then start, pause or resume, and a
box switched on mid-film joins at the right position. **SETUP.md** walks
through it step by step.

## Settings worth knowing

| Setting | What it does |
| --- | --- |
| **Show notifications** | On-screen popups when mirroring starts, when a device cannot be reached, and when a device joins the sync group. A notice that a device left is always shown. |
| **Start timeout (seconds)** | How long the leader holds before giving up. Raise it if the follower is a slow box or the library is remote. |
| **Follower head start (ms)** | How far ahead of the leader the follower is released. Raise it if the follower is consistently *behind*; lower it if it's ahead. |
| **Periodically re-align** | Off by default. Turn on if the two screens are within earshot and slow drift bothers you. |
| **When this box stops** | Leader/Follower only: what the follower does when the leader stops or exits, stop too (default), pause, or keep playing. A Sync Group always pauses the others and tells them who left. |

## Limits, honestly

- In Leader/Follower mode control is one-way: pausing on the **follower** does
  not pause the leader. Use Sync Group mode if you want every box's remote to
  work.
- Sync is loose. Expect them to start within about half a second of each other
  and to stay within a second or two; frame-accurate sync isn't achievable over
  JSON-RPC polling.
- Both boxes need to reach each other on the LAN (no client isolation / VLAN
  blocking between them).
- Kodi's web server is plain HTTP, so the follower's username and password go
  over the wire base64-encoded, not encrypted. That's fine on a home LAN; don't
  reuse a password that matters, and don't expose the follower's web port to
  the internet.
- Trick-play on the follower (its own seeking) will desync it until the next
  pause/resume on the leader.
- Works in both Jellyfin addon modes, "add-on" (dynamic) and library-sync
  (native), but both boxes must be in the *same* mode.

## Swapping roles

Change **This box is the** on each box. No restart needed; the addon reloads
settings when you close the dialog.

## Troubleshooting

Everything is logged with the `[service.jellyfin.dualplay]` prefix in
`kodi.log`, enable debug logging and search for that.

- *"Follower unreachable"* → check the Control settings above, and that the port
  and credentials match.
- *"Follower did not start playback"* → usually the follower's Jellyfin addon
  isn't signed in to the same server, or the item id doesn't exist for that user.
- *Follower plays but the leader's audio is ahead* → raise **Follower head
  start**.

## Tests

`python3 tests/test_syncgroup.py` runs the sync-group state machine against
stubbed Kodi boxes (no Kodi needed): host election, echo suppression, pause and
resume from non-host devices, departure notices, late join, dead-device
back-off.

MIT licensed.
