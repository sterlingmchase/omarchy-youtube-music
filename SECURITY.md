# Security Policy

## Supported versions

Security fixes are provided for the latest release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not include authentication cookies, keyring contents, browser profiles, or
other account data in a public issue.

## Authentication model

The plugin opens a dedicated Chromium profile for Google authentication and
copies only `youtube.com` session cookies into Secret Service. During playback,
it writes those cookies to a mode-0600 file inside the user's private
`XDG_RUNTIME_DIR`; **Forget login** removes that file and the keyring entry.

The plugin never requests elevated privileges. It runs with the user's normal
desktop permissions, as all Omarchy shell plugins do.
