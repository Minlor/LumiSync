import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from lumisync.sync import artwork, mpris_artwork


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeBus:
    """In-memory stand-in for the session bus: name -> (status, metadata)."""

    def __init__(self, players, extra_names=()):
        self.players = players
        self.extra_names = list(extra_names)
        self.metadata_reads = []

    def list_names(self):
        return [*self.extra_names, *self.players]

    def get_property(self, destination, name):
        status, metadata = self.players[destination]
        if name == "PlaybackStatus":
            return status
        if name == "Metadata":
            self.metadata_reads.append(destination)
            return metadata
        return None


def player(name):
    return f"org.mpris.MediaPlayer2.{name}"


class PlayerChoiceTests(unittest.TestCase):
    def test_playing_player_beats_paused_one(self):
        bus = FakeBus(
            {
                player("aaa"): ("Paused", {"mpris:artUrl": "file:///paused.png"}),
                player("zzz"): ("Playing", {"mpris:artUrl": "file:///playing.png"}),
            }
        )
        self.assertEqual(mpris_artwork.current_art_url(bus), "file:///playing.png")

    def test_paused_player_is_used_when_nothing_plays(self):
        bus = FakeBus(
            {
                player("stopped"): ("Stopped", {"mpris:artUrl": "file:///s.png"}),
                player("paused"): ("Paused", {"mpris:artUrl": "file:///p.png"}),
            }
        )
        self.assertEqual(mpris_artwork.current_art_url(bus), "file:///p.png")

    def test_no_player_or_only_stopped_players_give_none(self):
        self.assertIsNone(mpris_artwork.current_art_url(FakeBus({})))
        self.assertIsNone(
            mpris_artwork.current_art_url(
                FakeBus({player("x"): ("Stopped", {"mpris:artUrl": "file:///x"})})
            )
        )
        self.assertIsNone(mpris_artwork.current_art_url(None))

    def test_paused_art_is_not_used_when_the_playing_player_has_none(self):
        bus = FakeBus(
            {
                player("a"): ("Paused", {"mpris:artUrl": "file:///old-song.png"}),
                player("b"): ("Playing", {"xesam:title": "no cover"}),
            }
        )
        self.assertIsNone(mpris_artwork.current_art_url(bus))

    def test_non_mpris_names_are_ignored(self):
        bus = FakeBus({}, extra_names=["org.freedesktop.DBus", ":1.4"])
        self.assertIsNone(mpris_artwork.current_art_url(bus))

    def test_malformed_metadata_gives_none(self):
        for metadata in (None, "x", [], {"mpris:artUrl": 7}, {"mpris:artUrl": "  "}):
            with self.subTest(metadata=metadata):
                bus = FakeBus({player("p"): ("Playing", metadata)})
                self.assertIsNone(mpris_artwork.current_art_url(bus))

    def test_bus_errors_are_quiet(self):
        class Broken:
            def list_names(self):
                raise RuntimeError("bus went away")

            def get_property(self, destination, name):
                raise AssertionError("not reached")

        self.assertIsNone(mpris_artwork.current_art_url(Broken()))


class RecordingRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append((list(command), timeout))
        for needle, output in self.outputs:
            if needle in command:
                return output
        return None


class BusctlClientTests(unittest.TestCase):
    def test_reads_names_status_and_art_url_from_json(self):
        metadata = {
            "type": "a{sv}",
            "data": {
                "mpris:trackid": {"type": "o", "data": "/t/1"},
                "mpris:artUrl": {"type": "s", "data": "https://i.example/c.jpg"},
                "xesam:artist": {"type": "as", "data": ["A", "B"]},
            },
        }
        runner = RecordingRunner(
            [
                ("ListNames", json.dumps({"type": "as", "data": [["org.freedesktop.DBus", player("spotify")]]})),
                ("PlaybackStatus", json.dumps({"type": "s", "data": "Playing"})),
                ("Metadata", json.dumps(metadata)),
            ]
        )
        client = mpris_artwork.BusctlClient(runner=runner)
        self.assertEqual(mpris_artwork.current_art_url(client), "https://i.example/c.jpg")
        for command, timeout in runner.commands:
            self.assertEqual(command[:3], ["busctl", "--user", "--json=short"])
            self.assertLessEqual(timeout, 2.0)

    def test_garbage_output_gives_none(self):
        runner = RecordingRunner([("ListNames", "not json")])
        client = mpris_artwork.BusctlClient(runner=runner)
        self.assertIsNone(mpris_artwork.current_art_url(client))

    def test_timed_out_tool_gives_none(self):
        client = mpris_artwork.BusctlClient(runner=lambda command, timeout: None)
        self.assertIsNone(mpris_artwork.current_art_url(client))


class GdbusClientTests(unittest.TestCase):
    def test_parses_gvariant_text(self):
        runner = RecordingRunner(
            [
                ("org.freedesktop.DBus.ListNames", f"(['org.freedesktop.DBus', '{player('vlc')}', ':1.5'],)\n"),
                ("PlaybackStatus", "(<'Playing'>,)\n"),
                (
                    "Metadata",
                    "(<{'mpris:trackid': <objectpath '/t/1'>, 'mpris:artUrl': "
                    "<\"file:///tmp/it's%20b.png\">, 'xesam:title': <'x'>}>,)\n",
                ),
            ]
        )
        client = mpris_artwork.GdbusClient(runner=runner)
        self.assertEqual(
            mpris_artwork.current_art_url(client), "file:///tmp/it's%20b.png"
        )

    def test_unescapes_gvariant_strings(self):
        runner = RecordingRunner(
            [("Metadata", "(<{'mpris:artUrl': <'file:///a\\'b\\u00e9.png'>}>,)")]
        )
        client = mpris_artwork.GdbusClient(runner=runner)
        self.assertEqual(
            client.get_property(player("p"), "Metadata"),
            {"mpris:artUrl": "file:///a'bé.png"},
        )

    def test_metadata_without_art_is_empty(self):
        runner = RecordingRunner([("Metadata", "(<@a{sv} {}>,)")])
        client = mpris_artwork.GdbusClient(runner=runner)
        self.assertEqual(client.get_property(player("p"), "Metadata"), {})


class FileUrlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_reads_percent_encoded_file_url(self):
        path = self.root / "cover art.png"
        path.write_bytes(PNG)
        self.assertEqual(mpris_artwork.load_art_url(path.as_uri()), PNG)

    def test_file_over_the_cap_is_refused(self):
        path = self.root / "big.png"
        path.write_bytes(PNG * 4)
        self.assertIsNone(mpris_artwork.load_art_url(path.as_uri(), limit=len(PNG)))

    def test_directory_missing_file_and_remote_host_are_refused(self):
        self.assertIsNone(mpris_artwork.load_art_url(self.root.as_uri()))
        self.assertIsNone(mpris_artwork.load_art_url((self.root / "nope.png").as_uri()))
        self.assertIsNone(mpris_artwork.load_art_url("file://elsewhere/share/c.png"))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs POSIX FIFOs")
    def test_fifo_is_refused_without_blocking(self):
        fifo = self.root / "cover.png"
        getattr(os, "mkfifo")(fifo)
        self.assertIsNone(mpris_artwork.load_art_url(fifo.as_uri()))


class DataUrlTests(unittest.TestCase):
    def test_base64_image(self):
        url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        self.assertEqual(mpris_artwork.load_art_url(url), PNG)

    def test_percent_encoded_image(self):
        self.assertEqual(
            mpris_artwork.load_art_url("data:image/svg+xml,%3Csvg%2F%3E"), b"<svg/>"
        )

    def test_non_image_malformed_and_oversized_are_refused(self):
        encoded = base64.b64encode(PNG).decode()
        self.assertIsNone(mpris_artwork.load_art_url("data:text/plain;base64," + encoded))
        self.assertIsNone(mpris_artwork.load_art_url("data:image/png;base64,@@@"))
        self.assertIsNone(mpris_artwork.load_art_url("data:image/png;base64"))
        self.assertIsNone(
            mpris_artwork.load_art_url("data:image/png;base64," + encoded, limit=8)
        )

    def test_unknown_schemes_are_refused(self):
        for url in ("ftp://host/c.png", "smb://host/c.png", "/tmp/c.png", "", "attachment:1"):
            with self.subTest(url=url):
                self.assertIsNone(mpris_artwork.load_art_url(url))


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(PNG,), redirect=False):
        self.status_code = status
        self.headers = headers if headers is not None else {"Content-Type": "image/png"}
        self.chunks = list(chunks)
        self.is_redirect = redirect
        self.read_chunks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            self.read_chunks += 1
            yield chunk


class HttpUrlTests(unittest.TestCase):
    def fetch(self, responses, url="https://img.example/c.png", **kwargs):
        queue = list(responses)
        calls = []

        def fake_get(target, **options):
            calls.append((target, options))
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with mock.patch.object(requests, "get", side_effect=fake_get):
            result = mpris_artwork.load_art_url(url, **kwargs)
        return result, calls

    def test_image_is_fetched_without_following_redirects_automatically(self):
        result, calls = self.fetch([FakeResponse()])
        self.assertEqual(result, PNG)
        options = calls[0][1]
        self.assertFalse(options["allow_redirects"])
        self.assertTrue(options["stream"])
        self.assertLessEqual(max(options["timeout"]), mpris_artwork.HTTP_TIMEOUT)

    def test_http_redirect_is_followed_but_not_to_other_schemes(self):
        moved = FakeResponse(status=302, headers={"Location": "/real.png"}, redirect=True)
        result, calls = self.fetch([moved, FakeResponse()])
        self.assertEqual(result, PNG)
        self.assertEqual(calls[1][0], "https://img.example/real.png")

        to_file = FakeResponse(status=302, headers={"Location": "file:///etc/passwd"}, redirect=True)
        result, calls = self.fetch([to_file])
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_too_many_redirects_give_none(self):
        loop = [
            FakeResponse(status=301, headers={"Location": "/again"}, redirect=True)
            for _ in range(mpris_artwork.MAX_HTTP_REDIRECTS + 1)
        ]
        result, _calls = self.fetch(loop)
        self.assertIsNone(result)

    def test_non_images_and_errors_give_none(self):
        self.assertIsNone(self.fetch([FakeResponse(headers={"Content-Type": "text/html"})])[0])
        self.assertIsNone(self.fetch([FakeResponse(headers={})])[0])
        self.assertIsNone(self.fetch([FakeResponse(status=404)])[0])
        self.assertIsNone(self.fetch([requests.Timeout("slow")])[0])
        self.assertIsNone(self.fetch([requests.ConnectionError("down")])[0])

    def test_size_cap_is_enforced_while_streaming(self):
        streamed = FakeResponse(chunks=[b"x" * 10] * 100)
        result, _ = self.fetch([streamed], limit=25)
        self.assertIsNone(result)
        self.assertLess(streamed.read_chunks, 10)

        declared = FakeResponse(
            headers={"Content-Type": "image/jpeg", "Content-Length": "999999"}
        )
        result, _ = self.fetch([declared], limit=100)
        self.assertIsNone(result)
        self.assertEqual(declared.read_chunks, 0)

    def test_total_deadline_stops_a_slow_trickle(self):
        readings = iter([0.0, 0.0, 0.5, 1.0, 5.0, 5.0, 5.0, 5.0])
        fake_time = mock.Mock(monotonic=lambda: next(readings))
        slow = FakeResponse(chunks=[b"x"] * 6)
        with mock.patch.object(mpris_artwork, "time", fake_time):
            result, _ = self.fetch([slow], http_timeout=3.0)
        self.assertIsNone(result)
        self.assertEqual(slow.read_chunks, 3)


class CacheTests(unittest.TestCase):
    def test_each_url_is_loaded_once(self):
        loads = []

        def loader(url):
            loads.append(url)
            return PNG

        cache = mpris_artwork.ArtworkCache(loader=loader)
        self.assertEqual(cache.get("file:///a.png"), PNG)
        self.assertEqual(cache.get("file:///a.png"), PNG)
        self.assertEqual(cache.get("file:///b.png"), PNG)
        self.assertEqual(loads, ["file:///a.png", "file:///b.png"])

    def test_failures_are_retried_after_a_while(self):
        now = [0.0]
        loads = []

        def loader(url):
            loads.append(url)
            return None

        cache = mpris_artwork.ArtworkCache(loader=loader, clock=lambda: now[0])
        self.assertIsNone(cache.get("https://x/c.png"))
        self.assertIsNone(cache.get("https://x/c.png"))
        self.assertEqual(len(loads), 1)
        now[0] = mpris_artwork.FAILURE_RETRY_SECONDS + 1
        cache.get("https://x/c.png")
        self.assertEqual(len(loads), 2)

    def test_cache_is_bounded(self):
        cache = mpris_artwork.ArtworkCache(loader=lambda url: PNG, size=2)
        for index in range(5):
            cache.get(f"file:///{index}.png")
        self.assertEqual(len(cache._entries), 2)

    def test_read_artwork_bytes_uses_the_cache(self):
        bus = FakeBus({player("p"): ("Playing", {"mpris:artUrl": "file:///c.png"})})
        loads = []
        cache = mpris_artwork.ArtworkCache(loader=lambda url: loads.append(url) or PNG)
        for _ in range(3):
            self.assertEqual(
                mpris_artwork.read_artwork_bytes(lambda: bus, cache=cache), PNG
            )
        self.assertEqual(loads, ["file:///c.png"])

    def test_read_artwork_bytes_without_bus_tool_is_none(self):
        self.assertIsNone(mpris_artwork.read_artwork_bytes(lambda: None))


class PlatformDispatchTests(unittest.TestCase):
    def test_linux_reads_mpris(self):
        with mock.patch.object(artwork.sys, "platform", "linux"), mock.patch.object(
            mpris_artwork, "read_artwork_bytes", return_value=PNG
        ) as reader:
            self.assertEqual(artwork.read_current_artwork_bytes(), PNG)
        reader.assert_called_once()

    def test_linux_failure_is_quiet(self):
        with mock.patch.object(artwork.sys, "platform", "linux"), mock.patch.object(
            mpris_artwork, "read_artwork_bytes", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(artwork.read_current_artwork_bytes())

    def test_windows_still_uses_the_media_session(self):
        async def fake_session():
            return PNG

        with mock.patch.object(artwork.sys, "platform", "win32"), mock.patch.object(
            artwork, "_current_artwork_bytes", fake_session
        ), mock.patch.object(mpris_artwork, "read_artwork_bytes") as reader:
            self.assertEqual(artwork.read_current_artwork_bytes(), PNG)
        reader.assert_not_called()

    def test_other_platforms_have_no_artwork(self):
        with mock.patch.object(artwork.sys, "platform", "darwin"), mock.patch.object(
            mpris_artwork, "read_artwork_bytes"
        ) as reader:
            self.assertIsNone(artwork.read_current_artwork_bytes())
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
