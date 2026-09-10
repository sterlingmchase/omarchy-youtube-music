#!/usr/bin/env python3
"""Authenticated YouTube Music API and mpv playback backend."""

from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import stat
import re
from typing import Any



SCRIPT_DIR = Path(__file__).resolve().parent
BRIDGE_SPEC = importlib.util.spec_from_file_location("ytm_browser_bridge", SCRIPT_DIR / "browser_bridge.py")
if BRIDGE_SPEC is None or BRIDGE_SPEC.loader is None:
    raise RuntimeError("Could not load browser bridge")
bridge = importlib.util.module_from_spec(BRIDGE_SPEC)
BRIDGE_SPEC.loader.exec_module(bridge)

STATE_DIR = bridge.STATE_DIR
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "sterling.youtube-music"
MPV_SOCKET = RUNTIME_DIR / "mpv.sock"
RUNTIME_COOKIES = RUNTIME_DIR / "youtube-cookies.txt"
QUEUE_PATH = STATE_DIR / "queue.json"
PLAYING_QUEUE = STATE_DIR / "playing.json"


def private_runtime() -> None:
    parent = RUNTIME_DIR.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("A private desktop runtime directory is required")
    RUNTIME_DIR.mkdir(mode=0o700, exist_ok=True)
    info = RUNTIME_DIR.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("Unsafe player runtime directory")
    os.chmod(RUNTIME_DIR, 0o700)

# YouTube Music's current WEB_REMIX search filters, matching ytmusicapi.
SEARCH_PARAMS = {
    "songs": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D",
    "albums": "EgWKAQIYAWoKEAkQChAFEAMQBA%3D%3D",
    "artists": "EgWKAQIgAWoKEAkQChAFEAMQBA%3D%3D",
}


def load_auth() -> tuple[dict[str, Any], requests.cookies.RequestsCookieJar]:
    import requests
    auth = bridge.load_secret()
    jar = requests.cookies.RequestsCookieJar()
    for cookie in auth.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        if not bridge.youtube_domain(str(cookie.get("domain", ""))):
            continue
        jar.set(
            str(cookie.get("name", "")),
            str(cookie.get("value", "")),
            domain=str(cookie.get("domain", ".youtube.com")),
            path=str(cookie.get("path", "/")),
            secure=bool(cookie.get("secure", False)),
            expires=max(0, int(float(cookie.get("expires", 0) or 0))) or None,
        )
    return auth, jar


def cookie_value(jar: requests.cookies.RequestsCookieJar, names: tuple[str, ...]) -> str:
    for name in names:
        for cookie in jar:
            if cookie.name == name:
                return cookie.value
    return ""


def api_request(endpoint: str, payload: dict[str, Any]) -> Any:
    import requests
    auth, jar = load_auth()
    origin = "https://music.youtube.com"
    sapisid = cookie_value(jar, ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"))
    if not sapisid:
        raise RuntimeError("Stored YouTube Music session is incomplete; connect again")
    timestamp = str(int(time.time()))
    digest = hashlib.sha1(f"{timestamp} {sapisid} {origin}".encode("utf-8")).hexdigest()
    context = auth.get("context") or {}
    client = context.get("client") or {}
    body = dict(payload)
    body["context"] = context
    headers = {
        "Authorization": f"SAPISIDHASH {timestamp}_{digest}",
        "Content-Type": "application/json",
        "Origin": origin,
        "X-Origin": origin,
        "User-Agent": str(auth.get("userAgent") or "Mozilla/5.0"),
        "X-Goog-AuthUser": str(auth.get("sessionIndex") or "0"),
        "X-Youtube-Client-Version": str(client.get("clientVersion") or ""),
    }
    visitor = client.get("visitorData")
    if visitor:
        headers["X-Goog-Visitor-Id"] = str(visitor)
    try:
        response = requests.post(
        f"{origin}/youtubei/v1/{endpoint}",
        params={"prettyPrint": "false", "key": auth.get("apiKey")},
        json=body,
        headers=headers,
        cookies=jar,
        timeout=12,
        allow_redirects=False,
        )
    except requests.RequestException as error:
        raise RuntimeError("Could not reach YouTube Music. Check your connection and retry.") from error
    if response.status_code in {401, 403}:
        raise RuntimeError("YouTube Music session expired; connect again")
    if response.status_code != 200:
        raise RuntimeError(f"YouTube Music request failed (HTTP {response.status_code}). Try again.")
    return response.json()


def search(query: str, search_type: str) -> list[dict[str, str]]:
    if search_type not in SEARCH_PARAMS:
        raise RuntimeError("Search type must be songs, albums, or artists")
    data = api_request("search", {"query": query, "params": SEARCH_PARAMS[search_type]})
    kind = search_type[:-1]
    items = bridge.extract_items(data, kind, 8)
    save_queue(items)
    return items


def library() -> list[dict[str, str]]:
    data = api_request("browse", {"browseId": "FEmusic_liked_playlists"})
    items = bridge.extract_items(data, "playlist", 12)
    save_queue(items)
    return items


def browse(browse_id: str) -> list[dict[str, str]]:
    data = api_request("browse", {"browseId": browse_id})
    items = bridge.extract_items(data, "song", 30)
    save_queue(items)
    return items


def save_queue(items: list[dict[str, str]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    bridge.atomic_private_write(QUEUE_PATH, json.dumps({"items": items}, separators=(",", ":")) + "\n")


def load_queue() -> list[dict[str, str]]:
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return []


def mpv_request(command: list[Any], timeout: float = 2.0) -> Any:
    if not MPV_SOCKET.exists():
        raise RuntimeError("Nothing is playing")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(MPV_SOCKET))
        client.sendall((json.dumps({"command": command, "request_id": 1}) + "\n").encode("utf-8"))
        data = b""
        deadline = time.monotonic() + timeout
        while True:
            client.settimeout(max(0.001, deadline - time.monotonic()))
            chunk = client.recv(65536)
            if not chunk:
                raise RuntimeError("Player disconnected")
            data += chunk
            while b"\n" in data:
                line, data = data.split(b"\n", 1)
                response = json.loads(line)
                if response.get("request_id") != 1:
                    continue
                if response.get("error") != "success":
                    raise RuntimeError(str(response.get("error", "Player command failed")))
                return response.get("data")
            if time.monotonic() >= deadline or len(data) > 1048576:
                raise RuntimeError("Player response timed out")
    finally:
        client.close()


def ensure_mpv() -> None:
    private_runtime()
    if MPV_SOCKET.exists():
        try:
            mpv_request(["get_property", "idle-active"], 0.3)
            return
        except Exception:
            MPV_SOCKET.unlink(missing_ok=True)
    auth = bridge.load_secret()
    lines = ["# Netscape HTTP Cookie File", "# session-only YouTube cookies"]
    for cookie in auth.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain", ".youtube.com"))
        if not bridge.youtube_domain(domain):
            continue
        if any(any(c in str(cookie.get(key, "")) for c in "\t\r\n") for key in ("domain", "path", "name", "value")):
            raise RuntimeError("Invalid stored cookie format; reconnect your account")
        lines.append("\t".join([
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            str(cookie.get("path", "/")),
            "TRUE" if cookie.get("secure") else "FALSE",
            str(max(0, int(float(cookie.get("expires", 0) or 0)))),
            str(cookie.get("name", "")),
            str(cookie.get("value", "")),
        ]))
    bridge.atomic_private_write(RUNTIME_COOKIES, "\n".join(lines) + "\n")
    command = [
        "mpv",
        "--no-config",
        "--no-video",
        "--idle=yes",
        "--really-quiet",
        "--cache=yes",
        "--cache-pause=no",
        "--cache-secs=30",
        "--loop-file=no",
        "--loop-playlist=no",
        f"--input-ipc-server={MPV_SOCKET}",
        "--ytdl-format=bestaudio/best",
        f"--ytdl-raw-options=cookies={RUNTIME_COOKIES}",
    ]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if MPV_SOCKET.exists():
            return
        time.sleep(0.05)
    raise RuntimeError("mpv did not start")


def play(video_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise RuntimeError("Invalid YouTube track")
    load_auth()
    queue = [item for item in load_queue() if item.get("kind") == "song"]
    selected = next((index for index, item in enumerate(queue) if item.get("id") == video_id), 0)
    if not queue or queue[selected].get("id") != video_id:
        queue = [{"kind": "song", "id": video_id, "title": "YouTube Music", "subtitle": "", "thumbnail": ""}]
        selected = 0
    ensure_mpv()
    mpv_request(["playlist-clear"])
    ordered = queue[selected:] + queue[:selected]
    bridge.atomic_private_write(PLAYING_QUEUE, json.dumps({"items": ordered}))
    for index, item in enumerate(ordered):
        # Start only the selected track. `append-play` can restart loading for
        # every queued item while the first network stream is still opening.
        mode = "replace" if index == 0 else "append"
        url = "https://music.youtube.com/watch?v=" + str(item["id"])
        mpv_request(["loadfile", url, mode, -1, {"force-media-title": str(item.get("title") or "YouTube Music")}])


def property_value(name: str, fallback: Any = None) -> Any:
    try:
        value = mpv_request(["get_property", name], 0.4)
        return fallback if value is None else value
    except Exception:
        return fallback


def loop_enabled(value: Any) -> bool:
    return value is True or value == "inf" or (isinstance(value, int) and value > 0)


def repeat_mode() -> str:
    saved = property_value("user-data/ytm-repeat", "")
    if saved in {"off", "all", "one"}:
        return str(saved)
    if loop_enabled(property_value("loop-file", False)):
        return "one"
    if loop_enabled(property_value("loop-playlist", False)):
        return "all"
    return "off"


def status(check_auth: bool = False) -> dict[str, Any]:
    auth_state = {"connected": bridge.has_secret()} if check_auth else {}
    if not MPV_SOCKET.exists():
        return {**auth_state, "active": False, "playing": False}
    idle = bool(property_value("idle-active", True))
    if idle:
        return {**auth_state, "active": False, "playing": False}
    return {
        **auth_state,
        "active": not idle,
        "playing": not idle and not bool(property_value("pause", False)),
        "title": str(property_value("media-title", "YouTube Music")),
        "artist": str(property_value("metadata/by-key/ARTIST", "")),
        "album": str(property_value("metadata/by-key/ALBUM", "")),
        "position": float(property_value("time-pos", 0) or 0),
        "duration": float(property_value("duration", 0) or 0),
        "shuffle": bool(property_value("user-data/ytm-shuffle", False)),
        "repeat": repeat_mode(),
    }


def control(action: str, argument: str = "") -> None:
    if action == "toggle":
        mpv_request(["cycle", "pause"])
    elif action == "next":
        mpv_request(["playlist-next", "force"])
    elif action == "previous":
        mpv_request(["playlist-prev", "force"])
    elif action == "seek":
        mpv_request(["set_property", "time-pos", max(0, float(argument))])
    elif action == "shuffle":
        enabled = bool(property_value("user-data/ytm-shuffle", False))
        mpv_request(["playlist-unshuffle" if enabled else "playlist-shuffle"])
        mpv_request(["set_property", "user-data/ytm-shuffle", not enabled])
    elif action == "repeat":
        current = repeat_mode()
        next_mode = argument if argument in {"off", "all", "one"} else {
            "off": "all", "all": "one", "one": "off"
        }[current]
        mpv_request(["set_property", "loop-playlist", "inf" if next_mode == "all" else "no"])
        mpv_request(["set_property", "loop-file", "inf" if next_mode == "one" else "no"])
        mpv_request(["set_property", "user-data/ytm-repeat", next_mode])
    else:
        raise RuntimeError("Unknown playback action")


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    first = sys.argv[2] if len(sys.argv) > 2 else ""
    second = sys.argv[3] if len(sys.argv) > 3 else ""
    if action == "status":
        print(json.dumps(status(first == "auth")))
    elif action == "search":
        print(json.dumps({"items": search(first, second or "songs")}))
    elif action == "library":
        print(json.dumps({"items": library()}))
    elif action == "browse":
        print(json.dumps({"items": browse(first)}))
    elif action == "play":
        play(first)
        print(json.dumps({"ok": True}))
    elif action == "warm":
        ensure_mpv()
        print(json.dumps({"ok": True}))
    elif action in {"toggle", "next", "previous", "seek", "shuffle", "repeat"}:
        control(action, first)
        print(json.dumps({"ok": True}))
    elif action == "disconnect":
        if MPV_SOCKET.exists():
            try:
                mpv_request(["quit"])
            except (OSError, RuntimeError):
                pass
        bridge.clear_secret()
        RUNTIME_COOKIES.unlink(missing_ok=True)
        QUEUE_PATH.unlink(missing_ok=True)
        PLAYING_QUEUE.unlink(missing_ok=True)
        print(json.dumps({"ok": True}))
    else:
        raise RuntimeError("Unknown backend action")
    return 0


if __name__ == "__main__":
    try:
        action = sys.argv[1] if len(sys.argv) > 1 else "status"
        if action not in {"status", "search", "library", "browse"}:
            private_runtime()
            with (RUNTIME_DIR / "control.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                raise SystemExit(main())
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
