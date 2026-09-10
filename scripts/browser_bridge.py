#!/usr/bin/env python3
"""Maintain one persistent YouTube Music Chromium app window.

Authentication starts in a dedicated Chromium profile. Once the user confirms
sign-in, this bridge stores only YouTube cookies plus Innertube configuration
in the desktop Secret Service keyring. No persistent plaintext cookie file is
created.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import time
import tempfile
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen


APP_URL = "https://music.youtube.com/"
DEBUG_HOST = "127.0.0.1"
DEBUG_PORT = 9333
HOME = Path.home()
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
STATE_DIR = DATA_HOME / "sterling.youtube-music"
PROFILE_DIR = STATE_DIR / "chromium-profile"
LOCK_PATH = STATE_DIR / "browser.lock"
SECRET_ATTRIBUTES = ["application", "sterling.youtube-music", "account", "youtube"]
SEARCH_PARAMS = {
    "songs": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D",
    "albums": "EgWKAQIYAWoKEAkQChAFEAMQBA%3D%3D",
    "artists": "EgWKAQIgAWoKEAkQChAFEAMQBA%3D%3D",
}


def chromium_binary() -> str:
    for candidate in ("chromium", "google-chrome-stable", "google-chrome", "brave"):
        path = shutil_which(candidate)
        if path:
            return path
    raise RuntimeError("No supported Chromium browser was found")


def shutil_which(command: str) -> str | None:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(folder) / command
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def get_json(path: str, timeout: float = 0.35) -> Any:
    with urlopen(f"http://{DEBUG_HOST}:{DEBUG_PORT}{path}", timeout=timeout) as response:
        return json.load(response)


def music_target() -> dict[str, Any] | None:
    try:
        targets = get_json("/json/list")
    except (OSError, URLError, ValueError):
        return None
    for target in targets:
        url = str(target.get("url", ""))
        if target.get("type") == "page" and url.startswith(APP_URL):
            return target
    return None


def youtube_window_exists() -> bool:
    """Recognize an existing YTM window, including a non-plugin browser."""
    try:
        result = subprocess.run(
            ["hyprctl", "clients", "-j"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        clients = json.loads(result.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False
    return any("youtube music" in str(client.get("title", "")).lower() for client in clients)


def focus_window() -> None:
    subprocess.run(
        ["hyprctl", "dispatch", "focuswindow", "title:.*YouTube Music.*"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch(url: str) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    browser = chromium_binary()
    command = [
        browser,
        f"--user-data-dir={PROFILE_DIR}",
        f"--remote-debugging-address={DEBUG_HOST}",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=http://localhost",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        f"--app={url}",
    ]
    subprocess.Popen(
        ["uwsm-app", "--", *command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def receive_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise RuntimeError("Chromium closed the local navigation connection")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_frame(sock: socket.socket) -> tuple[int, bytes]:
    header = receive_exact(sock, 2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", receive_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", receive_exact(sock, 8))[0]
    mask = receive_exact(sock, 4) if masked else b""
    body = receive_exact(sock, length)
    if masked:
        body = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
    return opcode, body


def websocket_command(websocket_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    parsed = urlparse(websocket_url)
    host = parsed.hostname or DEBUG_HOST
    port = parsed.port or DEBUG_PORT
    resource = parsed.path + ("?" + parsed.query if parsed.query else "")
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    sock = socket.create_connection((host, port), timeout=2)
    try:
        request = (
            f"GET {resource} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://localhost\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("Chromium rejected the local navigation connection")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        mask = secrets.token_bytes(4)
        header = bytearray([0x81])
        length = len(body)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
        sock.sendall(bytes(header) + mask + masked)
        while True:
            opcode, response_body = receive_frame(sock)
            if opcode == 0x8:
                raise RuntimeError("Chromium closed the local navigation connection")
            if opcode != 0x1:
                continue
            message = json.loads(response_body.decode("utf-8"))
            if message.get("id") == payload.get("id"):
                return message
    finally:
        sock.close()


def navigate(target: dict[str, Any], url: str) -> None:
    websocket_url = str(target.get("webSocketDebuggerUrl", ""))
    if not websocket_url:
        raise RuntimeError("YouTube Music window has no navigation endpoint")
    websocket_command(
        websocket_url,
        {"id": 1, "method": "Page.navigate", "params": {"url": url}},
    )


def evaluate(target: dict[str, Any], expression: str) -> Any:
    websocket_url = str(target.get("webSocketDebuggerUrl", ""))
    if not websocket_url:
        raise RuntimeError("YouTube Music window has no local API endpoint")
    response = websocket_command(
        websocket_url,
        {
            "id": 2,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        },
    )
    result = response.get("result", {})
    if result.get("exceptionDetails"):
        raise RuntimeError("YouTube Music rejected the internal request")
    remote = result.get("result", {})
    if remote.get("subtype") == "error":
        raise RuntimeError(str(remote.get("description", "YouTube Music request failed")))
    return remote.get("value")


def atomic_private_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".ytm-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def youtube_domain(domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return domain == "youtube.com" or domain.endswith(".youtube.com")


def store_secret(value: dict[str, Any]) -> None:
    payload = json.dumps(value, separators=(",", ":"))
    if len(payload.encode("utf-8")) >= 8192:
        raise RuntimeError("Session exceeds the keyring helper's size limit; login was not saved")
    result = subprocess.run(
        ["secret-tool", "store", "--label=YouTube Music for Omarchy", *SECRET_ATTRIBUTES],
        input=payload,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not save the session in your keyring")
    if load_secret() != value:
        raise RuntimeError("Saved login could not be verified. Please finish sign-in again.")


def load_secret() -> dict[str, Any]:
    result = subprocess.run(
        ["secret-tool", "lookup", *SECRET_ATTRIBUTES],
        text=True,
        capture_output=True,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Connect YouTube Music once before using the plugin")
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError("The saved YouTube Music session is invalid; connect again") from error
    if not isinstance(value, dict):
        raise RuntimeError("The saved YouTube Music session is invalid; connect again")
    return value


def has_secret() -> bool:
    try:
        load_secret()
        return True
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def clear_secret() -> None:
    result = subprocess.run(
        ["secret-tool", "clear", *SECRET_ATTRIBUTES],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not remove the saved session. Unlock your keyring and retry.")


def capture_auth(target: dict[str, Any]) -> None:
    config = evaluate(
        target,
        """({
          loggedIn: Boolean(window.ytcfg && window.ytcfg.get('LOGGED_IN')),
          apiKey: window.ytcfg && window.ytcfg.get('INNERTUBE_API_KEY'),
          context: window.ytcfg && window.ytcfg.get('INNERTUBE_CONTEXT'),
          sessionIndex: window.ytcfg && window.ytcfg.get('SESSION_INDEX'),
          userAgent: navigator.userAgent
        })""",
    )
    if not isinstance(config, dict) or not config.get("loggedIn"):
        raise RuntimeError("Finish signing in to YouTube Music first")
    websocket_url = str(target.get("webSocketDebuggerUrl", ""))
    response = websocket_command(websocket_url, {"id": 3, "method": "Network.getAllCookies"})
    all_cookies = response.get("result", {}).get("cookies", [])
    cookies = [
        cookie for cookie in all_cookies
        if youtube_domain(str(cookie.get("domain", "")))
    ]
    names = {str(cookie.get("name", "")) for cookie in cookies}
    if not names.intersection({"SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"}):
        raise RuntimeError("YouTube authentication cookies were not available")

    client = (config.get("context") or {}).get("client") or {}
    context = {"client": {key: client[key] for key in
        ("clientName", "clientVersion", "hl", "gl", "visitorData") if key in client}}
    cookies = [{key: cookie[key] for key in
        ("name", "value", "domain", "path", "secure", "expires") if key in cookie}
        for cookie in cookies]
    auth = {
        "version": 1,
        "apiKey": config.get("apiKey"),
        "context": context,
        "sessionIndex": config.get("sessionIndex", "0"),
        "userAgent": config.get("userAgent"),
        "capturedAt": int(time.time()),
        "cookies": cookies,
    }
    store_secret(auth)


def innertube_request(target: dict[str, Any], endpoint: str, payload: dict[str, Any]) -> Any:
    payload_json = json.dumps(payload, separators=(",", ":"))
    endpoint_json = json.dumps(endpoint)
    expression = f"""
      (async () => {{
        if (!window.ytcfg) throw new Error('YouTube Music is not ready');
        const key = window.ytcfg.get('INNERTUBE_API_KEY');
        const context = window.ytcfg.get('INNERTUBE_CONTEXT');
        const input = {payload_json};
        input.context = context;
        const response = await fetch('/youtubei/v1/' + {endpoint_json}
          + '?prettyPrint=false&key=' + encodeURIComponent(key), {{
          method: 'POST',
          credentials: 'include',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(input)
        }});
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return await response.json();
      }})()
    """
    return evaluate(target, expression)


def runs_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(str(run.get("text", "")) for run in runs if isinstance(run, dict)).strip()
    return str(value.get("simpleText", "")).strip()


def first_nested(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = first_nested(child, key)
            if found not in (None, "", []):
                return found
    elif isinstance(value, list):
        for child in value:
            found = first_nested(child, key)
            if found not in (None, "", []):
                return found
    return None


def renderer_title(renderer: dict[str, Any]) -> str:
    title = runs_text(renderer.get("title", {}))
    if title:
        return title
    columns = renderer.get("flexColumns", [])
    if columns:
        column = columns[0].get("musicResponsiveListItemFlexColumnRenderer", {})
        return runs_text(column.get("text", {}))
    return ""


def renderer_subtitle(renderer: dict[str, Any]) -> str:
    subtitle = runs_text(renderer.get("subtitle", {}))
    if subtitle:
        return subtitle
    columns = renderer.get("flexColumns", [])
    if len(columns) > 1:
        column = columns[1].get("musicResponsiveListItemFlexColumnRenderer", {})
        return runs_text(column.get("text", {}))
    return ""


def collect_renderers(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"musicResponsiveListItemRenderer", "musicTwoRowItemRenderer"} and isinstance(child, dict):
                rows.append(child)
            else:
                rows.extend(collect_renderers(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(collect_renderers(child))
    return rows


def extract_items(data: Any, kind: str, limit: int = 6) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for renderer in collect_renderers(data):
        title = renderer_title(renderer)
        if not title:
            continue
        video_id = str(first_nested(renderer, "videoId") or "")
        browse_id = str(first_nested(renderer, "browseId") or "")
        item_id = video_id if kind == "song" else browse_id
        if kind == "song" and not video_id:
            continue
        if kind == "playlist" and not browse_id.startswith("VL"):
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        thumbnails = first_nested(renderer, "thumbnails")
        thumbnail = ""
        if isinstance(thumbnails, list) and thumbnails:
            thumbnail = str(thumbnails[-1].get("url", ""))
        items.append(
            {
                "kind": kind,
                "id": item_id,
                "title": title,
                "subtitle": renderer_subtitle(renderer),
                "thumbnail": thumbnail,
            }
        )
        if len(items) >= limit:
            break
    return items


def wait_for_target(timeout: float = 7.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        target = music_target()
        if target:
            return target
        time.sleep(0.1)
    return None


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "ensure"
    url = sys.argv[2] if len(sys.argv) > 2 else APP_URL
    option = sys.argv[3] if len(sys.argv) > 3 else ""
    if action in {"ensure", "focus", "navigate"} and not url.startswith(APP_URL):
        raise RuntimeError("Refusing to navigate outside music.youtube.com")
    if action not in {"ensure", "focus", "navigate", "status", "capture", "disconnect", "search", "library", "play", "playlist"}:
        raise RuntimeError(f"Unknown action: {action}")

    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        os.chmod(LOCK_PATH, 0o600)
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        target = music_target()

        if action == "status":
            auth = "connected" if has_secret() else "disconnected"
            browser = "active" if target else ("external-active" if youtube_window_exists() else "inactive")
            print(json.dumps({"auth": auth, "browser": browser}))
            return 0

        if action == "disconnect":
            clear_secret()
            print(json.dumps({"auth": "disconnected"}))
            return 0

        if action == "capture":
            if target is None:
                raise RuntimeError("Open the dedicated YouTube Music sign-in window first")
            capture_auth(target)
            try:
                websocket_command(
                    str(target.get("webSocketDebuggerUrl", "")),
                    {"id": 4, "method": "Page.close"},
                )
            except (OSError, RuntimeError, ValueError):
                pass
            print(json.dumps({"auth": "connected"}))
            return 0

        if action in {"search", "library", "play", "playlist"} and target is None:
            print(json.dumps({"error": "Open YouTube Music and complete sign-in first"}))
            return 1

        if action == "search":
            search_type = option or "songs"
            if search_type not in SEARCH_PARAMS:
                raise RuntimeError("Search type must be songs, albums, or artists")
            data = innertube_request(target, "search", {
                "query": url,
                "params": SEARCH_PARAMS[search_type],
            })
            print(json.dumps({"items": extract_items(data, search_type[:-1])}))
            return 0

        if action == "library":
            data = innertube_request(target, "browse", {"browseId": "FEmusic_liked_playlists"})
            items = extract_items(data, "playlist")
            if not items and first_nested(data, "messageRenderer"):
                print(json.dumps({"error": "Sign in to YouTube Music to view your playlists"}))
                return 1
            print(json.dumps({"items": items}))
            return 0

        if action == "play":
            if not url.replace("-", "").replace("_", "").isalnum():
                raise RuntimeError("Invalid YouTube Music video id")
            navigate(target, APP_URL + "watch?v=" + url)
            focus_window()
            print("active")
            return 0

        if action == "playlist":
            playlist_id = url[2:] if url.startswith("VL") else url
            if not playlist_id.replace("-", "").replace("_", "").isalnum():
                raise RuntimeError("Invalid YouTube Music playlist id")
            navigate(target, APP_URL + "playlist?list=" + playlist_id)
            focus_window()
            print("active")
            return 0

        if action == "ensure":
            if target:
                print("active")
                return 0
            if youtube_window_exists():
                print("external-active")
                return 0
            launch(url)
            print("launched" if wait_for_target() else "launching")
            return 0

        if target is None:
            # An explicit navigation/focus action adopts the dedicated profile.
            launch(url)
            target = wait_for_target()
            if target is None:
                return 1
        elif action == "navigate":
            navigate(target, url)

        focus_window()
        print("active")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"youtube-music bridge: {error}", file=sys.stderr)
        raise SystemExit(1)
