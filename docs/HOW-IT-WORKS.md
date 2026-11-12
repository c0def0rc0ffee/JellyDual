# How it works

Jellyfin's own **SyncPlay** does not work in the Jellyfin for Kodi add-on. Only
the web client, Jellyfin Media Player and mpv-shim support it. This add-on does
the job a different way: it drives the other boxes over Kodi's built in
JSON-RPC API.

Every box must use the same Jellyfin server through the Jellyfin for Kodi
add-on.

## Leader and follower

One box is the **leader**, the other the **follower**. When you start something
on the leader, it:

1. Works out a path the follower can open, a `plugin://plugin.video.jellyfin/…`
   URL, so the follower streams from Jellyfin using its *own* session rather
   than piggybacking on the leader's.
2. Pauses itself, tells the follower to open that item, and waits for it to
   load.
3. Seeks the follower to the leader's position, starts it, gives it a small
   head start, then releases itself.

After that, pause, resume, seek and stop on the leader are forwarded to the
follower. Every resume also seeks the follower again, which quietly cleans up
any drift that crept in.

The add-on is installed on the follower too, but does nothing there. It is
installed so you can swap the roles later without reinstalling.

## Sync Group

Up to eleven boxes, each with its own name. Each box lists the others in ten
device slots.

- Whichever box you start a film on hosts it and opens it everywhere.
- Pause and resume from **any** box are echoed to all of them.
- Stopping or exiting anywhere pauses everyone else and tells them which
  device left.
- A box switched on mid film joins by itself at the right position.

### Echo suppression

Commands are announced over `JSONRPC.NotifyAll` before they land, so each box
can tell a group command from its own user's button press. That is what stops
mirrored commands being mirrored again forever.

See [SETUP.md](../SETUP.md#sync-group-many-boxes-start-and-control-from-any-of-them)
for what the group does in each case.
