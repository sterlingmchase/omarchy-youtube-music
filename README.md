# YouTube Music for Omarchy

A compact Omarchy bar player that uses a browser only for initial Google
authentication, then searches, browses, and plays from the plugin itself.

## What it does

- Opening the panel never opens a browser.
- The explicit sign-in button opens one dedicated Chromium app window for
  Google authentication; a launch lock prevents duplicate windows.
- After confirmation, only YouTube cookies and required client configuration
  are saved in the encrypted desktop keyring.
- Search Songs, Albums, or Artists and browse authenticated playlists inside
  the popup.
- Control play/pause, previous/next, seeking, shuffle, and repeat from the bar.
- Play audio through a private mpv process without a persistent browser window.
- **Forget login** stops the plugin player, clears its saved keyring session,
  and removes its runtime cookie file and saved queues. The separate Chromium
  profile remains signed in; this is not a Google account-wide sign-out.

The plugin uses YouTube Music's authenticated web API for metadata and mpv with
yt-dlp for audio playback. A mode-0600 cookie file is materialized only in the
per-login runtime directory while playback needs it; persistent credentials
remain in Secret Service.

Runtime cookies are readable by processes running as your user and remain until
Forget login or the desktop session ends. Secret Service protection depends on
your configured keyring; the plugin does not itself verify encryption at rest.
The authentication browser uses a loopback debugging endpoint, which other
processes running locally may access while that browser remains open.

Version 0.4.0 reduces background polling, avoids keyring reads during playback
updates, reports control errors, adds a bounded scrolling result list, checks
cookie-domain boundaries, improves first-play queue loading, and fixes repeat
state handling. Regression tests use synthetic data, not live account
credentials.

## Requirements

- Omarchy with the Quickshell bar
- Chromium (authentication only)
- Secret Service (`secret-tool`)
- mpv and yt-dlp
- Python 3 with requests

On Omarchy, install the runtime dependencies with:

```bash
omarchy pkg add chromium libsecret mpv yt-dlp python-requests
```

The isolated browser profile lives under
`~/.local/share/sterling.youtube-music/chromium-profile`. The reusable session
is stored by the desktop keyring under the application name
`sterling.youtube-music`.

## Install

```bash
omarchy plugin add https://github.com/sterlingmchase/omarchy-youtube-music.git --enable
```

Omarchy will prompt for the icon placement: **left**, **center**, or **right**.
You can move it later with:

```bash
omarchy bar move sterling.youtube-music --section right
```

## Remove

Use **Forget login** in the panel first. That stops the private player and
removes the plugin's keyring session, temporary cookie file, and queues. Then
remove the plugin:

```bash
omarchy plugin remove sterling.youtube-music
```

The isolated Chromium profile under
`~/.local/share/sterling.youtube-music/chromium-profile` is deliberately kept
so removing the plugin does not silently delete browser data. Delete that
directory yourself only if you also want to remove the dedicated browser login.

## Controls

- Left click: open/close panel (never launches the browser)
- Middle click: play/pause
- Right click: next track
- `/`: focus search
- `Space`: play/pause
- `n` / `p` or Right / Left: next / previous
- `s`: shuffle
- `r`: cycle repeat off / all / one

For the initial connection, click **1. Open sign-in**, complete Google sign-in,
then click **2. Finish sign-in**. The browser window closes after the session is
saved. Repeat these steps only when Google expires the session.

## Validate

```bash
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" BarWidget.qml Panel.qml
python3 -m unittest discover -s tests -v
```

## Support and security

This plugin uses YouTube Music's private web API, which can change without
notice. Please report bugs through the repository issue tracker. For security
issues, follow [SECURITY.md](SECURITY.md).

## License

MIT
