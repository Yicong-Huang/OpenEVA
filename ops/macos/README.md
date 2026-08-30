# macOS launchd service

`com.openeva.server.plist.example` runs the OpenEVA server as a background
LaunchAgent that survives logout and restarts on crash.

launchd does **not** expand `$HOME`, `~`, or any shell variable inside a plist,
so every path has to be absolute. Copy the example, substitute the two
placeholders, and load it:

```sh
sed -e "s#/path/to/OpenEVA#$PWD#g" -e "s#/Users/YOUR_USER#$HOME#g" \
  ops/macos/com.openeva.server.plist.example \
  > ~/Library/LaunchAgents/com.openeva.server.plist
mkdir -p ~/Library/Logs/OpenEVA
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openeva.server.plist
```

Unload with `launchctl bootout gui/$(id -u)/com.openeva.server`. A local copy at
`ops/macos/com.openeva.server.plist` (with your own paths filled in) is
gitignored, so you can keep one next to the template.
