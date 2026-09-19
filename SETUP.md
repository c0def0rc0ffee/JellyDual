# Jellyfin Dual Play: Setup Guide

Step-by-step setup. Current as of **v2.1.1**.

Two ways to run it:

- **Leader / Follower** (steps 1 to 4 below), exactly two boxes, one-way
  control. Simplest.
- **Sync Group** (see *Sync Group* section), up to 11 boxes, start a film on
  whichever one you like, control it from whichever one you're in front of.
  Slightly more setup: every box needs the Control settings on.
(Keep this file in step with the addon, update it whenever settings or
behaviour change.)

## What you need

- Two Kodi boxes (Kodi 19 or later) on the same LAN, able to reach each other
  (no client isolation / VLAN blocking between them).
- Both running the **Jellyfin for Kodi** addon, signed in to the **same
  Jellyfin server**, and in the **same mode**, either both "add-on" (dynamic)
  or both library-sync (native).
- The addon zip: `JellyDualPlay Dist/service.jellyfin.dualplay-v<version>.zip`.

Decide which box is the **leader** (the one you drive with a remote) and which
is the **follower** (the one that copies it). Control is one-way: the follower's
remote does not affect the leader.

## Step 1: Install on BOTH boxes

1. If not already on: *Settings → System → Add-ons → Unknown sources* → ON.
2. *Settings → Add-ons → Install from zip file* → pick the zip.

The addon is a background service; there's nothing to launch.

## Step 2: Follower box

1. *Settings → Services → Control*:
   - **Allow remote control via HTTP** → ON. Note the **port** (default 8080).
   - **Allow remote control from applications on other systems** → ON.
   - **Username / Password**: set them (Kodi requires a password by default;
     the username default is `kodi`). Note both.
2. *Settings → Add-ons → My add-ons → Services → Jellyfin Dual Play →
   Configure*:
   - **This box is the** → *Follower (receives commands)* (this is the
     default).

That's everything for the follower. Find its IP address under
*Settings → System information → Network*, you'll need it in the next step.
Consider giving it a fixed IP / DHCP reservation on your router so it doesn't
move.

## Step 3: Leader box

*Settings → Add-ons → My add-ons → Services → Jellyfin Dual Play → Configure*:

| Setting | Value |
| --- | --- |
| Enable dual play | ON |
| This box is the | **Leader (sends commands)** |
| When this box stops | **Stop the others** mirrors the stop (default). **Pause the others** holds their place, stop in the lounge, press play in the bedroom to carry on. **Leave them playing** lets them run to the end. Play/pause/seek always mirror. |
| Follower address | The follower's IP or hostname, e.g. `123.123.123.50`. Pasting `123.123.123.50:8080` or a full URL also works, port and path get split out. |
| Port | The port from step 2 (default 8080) |
| Username / Password | From step 2 (blank both if the follower has no auth) |

Each setting shows a fuller explanation at the bottom of the dialog when
highlighted.

Then press **Test connection**. Expected result: a dialog naming
the follower's Kodi version. If it fails, see Troubleshooting below.

## Step 4: Try it

Play something from the Jellyfin library on the leader. You should see:

1. The leader starts, then holds (paused) for a few seconds.
2. The follower starts the same item.
3. "Playing on both screens" notification; both play in loose sync.

From then on, pause / resume / seek / stop on the leader are mirrored. Every
resume also re-aligns the follower, so a quick pause+resume is the manual
"fix the sync" button.

## Timing settings (leader, *Timing* tab)

| Setting | Default | When to change |
| --- | --- | --- |
| Start timeout (seconds) | 25 | Raise for a slow follower box or remote server. |
| Follower head start (ms) | 400 | Raise if the follower is consistently **behind** the leader; lower if it's ahead. Applies at start-up and on every resume. |
| Periodically re-align | OFF | Turn on if both screens are within earshot and slow drift bothers you. |
| Check every (seconds) | 120 | How often the drift check runs. |
| Re-seek threshold (seconds) | 5 | Drift tolerance before a corrective seek. |

Dialling in sync: pause and resume while watching both screens. If the
follower lags, raise the head start by 100 to 200 ms and try again.

## Sync Group: many boxes, start and control from any of them

Every box is equal. Whichever one you start playback on becomes the **host**
for that film and opens it on all the others; from then on pause and resume
from **any** box are echoed to every box in the group, whoever the host is.

### Set-up on every box

1. Turn on both Control toggles (as in step 2 above) and set the **same
   username and password** on every box. Keeping the same web server port
   everywhere keeps things simple too.
2. Give each box a fixed IP or DHCP reservation.
3. *Configure* the addon, **Dual Play** tab:
   - **This box is the** → *Sync Group member*
   - **Port / Username / Password** → the shared Control credentials
4. **Sync Group** tab:
   - **Device name** → what this box is called in the group, e.g. `Lounge`.
     Blank uses Kodi's own device name.
   - **Device 1 … Device 10** → one slot per *other* box: a **Name**
     (e.g. `Bedroom`, optional) and its **Address** (e.g. `123.123.123.60`).
     Add `:port` to the address only for a box on a different port. Fill the
     slots in any order; blank slots are ignored.

So with three boxes: Lounge `.50`, Bedroom `.60`, Kitchen `.70`, the Lounge
lists Bedroom and Kitchen, the Bedroom lists Lounge and Kitchen, the Kitchen
lists Lounge and Bedroom. Ten slots means up to eleven boxes in a group.

**Test connection** (Dual Play tab) checks every filled slot and reports each
device by name, with whether it's playing or idle.

### What happens when

| You do this | The group does this |
| --- | --- |
| Start a film on any box | That box hosts it: opens it everywhere, syncs everyone, then plays. The others never start a session of their own. |
| Pause on **any** box, host or not | Everyone pauses. |
| Press play on **any** box | Everyone resumes, re-aligned to the box you pressed play on. |
| Seek on the host | Everyone follows. |
| Stop, or exit Kodi, on **any** box | Everyone else **pauses** and shows "*name* left the sync group". |
| Switch a box on mid-film | It finds the running film by itself within ~10 seconds and starts it at the right position. |
| Film reaches its end | Nothing special, every box finishes on its own. |

Moving rooms: stop (or just pause) where you are, walk, press play where you
land. Everyone re-aligns to the box you pressed play on.

### Sync Group notes

- Starting a film opens it on **all** members, there's no "just these rooms"
  selection for a session.
- The slowest box gates the start; any that fail are skipped with an
  "N of M devices started" notification.
- An unreachable box is skipped for 30 seconds after a failed command, so a
  powered-off room never stalls your remote presses.
- A box that comes online joins only a group that is actually **playing**. If
  everyone is paused, it stays out until someone presses play, deliberate, so
  a waking box can't unpause your house.
- A box already playing something else is never hijacked by the group.
- Departure notices ("… left the sync group") are shown even if you've turned
  **Show notifications** off.
- Drift correction runs on whichever box hosted the session.
- Every box needs the Control settings on, since each is now both a remote and
  remotely controlled.

## Swapping roles

Change **This box is the** on each box (and fill in the follower details on
the new leader). No restart needed, settings reload when the dialog closes.

## Security note

Kodi's web server is plain HTTP: the follower's username/password go over the
wire base64-encoded, not encrypted. Fine on a home LAN, but don't reuse a
password that matters, and never expose the follower's web port to the
internet.

## Troubleshooting

Everything the addon does is logged with the `[service.jellyfin.dualplay]`
prefix, enable *Settings → System → Logging → Enable debug logging* and
search `kodi.log` for it.

| Symptom | Fix |
| --- | --- |
| "Follower unreachable" / test fails | On the follower re-check both Control toggles; confirm the port; confirm username/password match exactly; try `http://<follower-ip>:<port>` in a browser from another machine, you should get a login prompt or Kodi's web interface. |
| "Follower did not start playback" | The follower's Jellyfin addon isn't signed in to the same server, or the item doesn't exist for that user (different libraries/permissions), or the boxes are in different Jellyfin modes (one add-on, one native). |
| Follower consistently behind | Raise **Follower head start**. |
| Follower consistently ahead | Lower **Follower head start**. |
| Slow drift over a long film | Turn on **Periodically re-align**, or just pause/resume once. |
| Follower keeps playing after leader stops | Check **When this box stops** is set to *Stop the follower*. If it is, that shouldn't happen since v1.0.1, check the log and the network. A hard power-off of the leader always leaves the follower playing: the stop/pause command is sent by the leader, and a dead box sends nothing. |
| Both boxes fight / playback restarts in a loop | Both boxes are set to Leader and pointed at each other. Set one to Follower. |
| Leader stuck paused after a failed start | Shouldn't happen since v1.0.1, press play; then check the log for the underlying peer error. |

## Updating the addon

Install the new zip over the old one (*Install from zip file* again) on
**both** boxes. Settings are kept.
