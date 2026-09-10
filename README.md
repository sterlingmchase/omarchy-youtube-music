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

Version 0.3.3 reduces background polling, avoids keyring reads during playback
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
qmllint -I "$OMARCHY_PATH/shell" Panel.qml
```

## License

MIT
