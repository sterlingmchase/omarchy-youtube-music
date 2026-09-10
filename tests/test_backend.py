import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backend", Path(__file__).parents[1] / "scripts/backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class BackendTests(unittest.TestCase):
    def test_cookie_domain_boundary(self):
        for domain in ("youtube.com", ".youtube.com", "music.youtube.com"):
            self.assertTrue(backend.bridge.youtube_domain(domain))
        for domain in ("evilyoutube.com", "youtube.com.evil.test", "google.com", ""):
            self.assertFalse(backend.bridge.youtube_domain(domain))

    def test_atomic_write_does_not_follow_destination_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            victim = Path(directory) / "victim"
            victim.write_text("unchanged")
            target = Path(directory) / "session"
            target.symlink_to(victim)
            backend.bridge.atomic_private_write(target, "private")
            self.assertEqual(victim.read_text(), "unchanged")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_status_does_not_read_keyring(self):
        with patch.object(backend.bridge, "has_secret", side_effect=AssertionError("keyring polled")):
            with patch.object(backend, "MPV_SOCKET", Path("/nonexistent/ytm.sock")):
                self.assertEqual(backend.status(), {"active": False, "playing": False})

    def test_ipc_ignores_events_and_handles_fragmented_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mpv.sock"
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            server.listen()
            def reply():
                client, _ = server.accept()
                with client:
                    request = json.loads(client.recv(4096))
                    client.sendall(b'{"event":"idle"}\n')
                    data = json.dumps({"request_id": request["request_id"], "error": "success", "data": 42}).encode() + b'\n'
                    client.sendall(data[:8])
                    client.sendall(data[8:])
            thread = threading.Thread(target=reply)
            thread.start()
            try:
                with patch.object(backend, "MPV_SOCKET", path):
                    self.assertEqual(backend.mpv_request(["get_property", "time-pos"]), 42)
            finally:
                thread.join(timeout=3)
                server.close()

    def test_runtime_rejects_shared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            with patch.object(backend, "RUNTIME_DIR", Path(directory) / "player"):
                with self.assertRaises(RuntimeError):
                    backend.private_runtime()

    def test_repeat_boolean_false_is_off(self):
        with patch.object(backend, "property_value", side_effect=lambda name, fallback=None: {
            "user-data/ytm-repeat": "", "loop-file": False, "loop-playlist": False
        }.get(name, fallback)):
            self.assertEqual(backend.repeat_mode(), "off")

    def test_repeat_cycle_updates_player_and_saved_state(self):
        commands = []
        with patch.object(backend, "repeat_mode", return_value="off"):
            with patch.object(backend, "mpv_request", side_effect=lambda command: commands.append(command)):
                backend.control("repeat")
        self.assertEqual(commands, [
            ["set_property", "loop-playlist", "inf"],
            ["set_property", "loop-file", "no"],
            ["set_property", "user-data/ytm-repeat", "all"],
        ])


if __name__ == "__main__":
    unittest.main()
