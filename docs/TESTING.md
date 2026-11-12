# Tests

None of the tests needs Kodi. Run them from the project folder:

```bash
python3 tests/test_syncgroup.py
python3 tests/test_peer.py
python3 tests/test_testscript.py
python3 tests/check_addon.py
```

- `tests/test_syncgroup.py` runs the Sync Group state machine against stubbed
  Kodi boxes: host election, echo suppression, pause and resume from devices
  that are not the host, departure notices, late join, back off from a dead
  device, the event queue and the departure sent on exit.
- `tests/test_peer.py` checks the JSON-RPC client against a throwaway server
  on loopback: address parsing, the auth header and every error path.
- `tests/test_testscript.py` checks the Test connection button's dialog text
  and its guard against a second run.
- `tests/check_addon.py` is the house rules check: `addon.xml` sanity, every
  Python file compiles, and no stray characters, credential shapes or
  private addresses in the text files.

`build-zip.sh` runs all of them and refuses to package on a failure.
