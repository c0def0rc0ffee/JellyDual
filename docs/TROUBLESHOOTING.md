# Troubleshooting

Everything is logged with the `[service.jellyfin.dualplay]` prefix in
`kodi.log`. Enable debug logging and search for that.

| Symptom | Fix |
| --- | --- |
| "Follower unreachable" | Check the follower's *Settings → Services → Control* toggles, and that the port and credentials match. |
| "Follower did not start playback" | Usually the follower's Jellyfin add-on is not signed in to the same server, or the item id does not exist for that user. |
| Follower plays but the leader's audio is ahead | Raise **Follower head start**. |
| Follower out of step after seeking on it | Seeking on the follower itself puts it out of step until the next pause and resume on the leader. |

The full table, with more symptoms, is in
[SETUP.md](../SETUP.md#troubleshooting).
