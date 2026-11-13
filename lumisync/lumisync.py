"""LumiSync entry point.

Default behavior (no args): launch the GUI.
Use ``--monitor`` or ``--music`` for headless sync.
"""

import argparse
import os
import sys
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable, List, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "lumisync"

from . import connection, devices, led_mapping
from .config.sync_settings import SyncSettings
from .device import as_device
from .utils.logging import setup_logger

logger = setup_logger("lumisync")


def _ensure_standard_streams() -> None:
    """Keep argparse usable in windowed frozen builds."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _launch_gui() -> int:
    """Import and run the GUI. Imported lazily so CLI mode skips GUI deps."""
    try:
        from .gui import run_gui
    except ImportError as e:
        logger.error("Failed to load GUI module", exc_info=True)
        print(f"GUI module failed to load: {e}")
        print("Run 'lumisync --check-gui' to diagnose the installation.")
        return 1
    run_gui()
    return 0


def _cli_sync_settings() -> SyncSettings:
    """Load the same persisted preferences the GUI uses."""
    try:
        data = devices.get_data(refresh=False)
        return SyncSettings.from_mapping(data.get("sync"))
    except Exception:
        logger.warning("Could not read sync settings; using defaults", exc_info=True)
        return SyncSettings.from_options()


def _discover_for_cli():
    """Return a server and the saved + discovered devices for headless sync."""
    saved = devices.get_data(refresh=False)
    saved_devices = list(saved.get("devices", []))
    server, discovered = connection.connect()
    if discovered:
        return server, devices.merge_discovered_devices(saved_devices, discovered)
    return server, saved_devices


def _gui_settings() -> Any:
    """The GUI's QSettings store, readable without a running GUI (or None)."""
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        return None
    try:
        override = os.environ.get("LUMISYNC_SETTINGS_INI", "").strip()
        if override:
            return QSettings(override, QSettings.Format.IniFormat)
        return QSettings("Minlor", "LumiSync")
    except Exception:
        return None


def _saved_mode_device_ids(mode: str) -> List[str]:
    """Device ids the GUI saved as this mode's sync targets (may be empty)."""
    settings = _gui_settings()
    if settings is None:
        return []
    try:
        value = settings.value(f"sync/{mode}/devices", None)
    except Exception:
        return []
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in value.split(",") if item]
    try:
        return [str(item) for item in value if item]
    except TypeError:
        return []


def _select_cli_targets(mode: str, candidates: Sequence[Any]) -> List[Any]:
    """Pick the devices to sync: local-streaming devices, the GUI's choice first."""
    from .drivers.registry import capabilities_for_device

    streaming = []
    for device in candidates:
        try:
            if capabilities_for_device(device).supports_streaming:
                streaming.append(device)
        except Exception:
            logger.warning("Skipping a device whose capabilities could not be read", exc_info=True)

    saved_ids = _saved_mode_device_ids(mode)
    if saved_ids:
        by_id = {}
        for device in streaming:
            by_id.setdefault(as_device(device).identity, device)
        chosen = [by_id[device_id] for device_id in saved_ids if device_id in by_id]
        if chosen:
            return chosen
        print(
            f"The {mode} sync targets saved in the app were not found; "
            "syncing every local device instead."
        )
    return streaming


def _cli_mapping_provider() -> Optional[Callable[[int, float], List[led_mapping.NormalizedRect]]]:
    """Read the LED mapping saved in the app's LED Mapping tab, like the GUI."""
    if _gui_settings() is None:
        return None

    def provider(segment_count: int, aspect_ratio: float) -> List[led_mapping.NormalizedRect]:
        settings = _gui_settings()
        if settings is None:
            return led_mapping.generate_screen_mapping(segment_count, aspect_ratio)
        return led_mapping.load_mapping_from_settings(settings, segment_count, aspect_ratio)

    return provider


def _watch_for_enter(prompt: str, pressed: Event) -> None:
    """Set ``pressed`` when Enter is hit; without a console, only Ctrl+C stops."""

    def wait() -> None:
        try:
            input(prompt)
        except (EOFError, OSError, RuntimeError):
            print("No console input available; press Ctrl+C to stop sync.")
            return
        pressed.set()

    Thread(target=wait, name="sync-input", daemon=True).start()


def _run_cli_sync(
    server,
    selected,
    mode: str,
    settings: SyncSettings,
    *,
    mapping_provider: Optional[Callable[[int, float], List[led_mapping.NormalizedRect]]] = None,
) -> int:
    """Run one session until Enter / Ctrl+C, or until the session ends by itself.

    Exit status is 0 when the user stopped a running sync (even if some
    devices failed to start) and 1 when no device started or the session
    ended on its own.
    """
    from .sync.session import SyncSession

    stop_event = Event()
    started = Event()
    user_stopped = Event()

    def on_started(count: int) -> None:
        started.set()

    session = SyncSession(
        mode,
        selected,
        settings,
        server=server,
        stop_event=stop_event,
        mapping_provider=mapping_provider,
        on_status=print,
        on_error=print,
        on_started=on_started,
    )

    def run():
        try:
            session.run()
        except Exception as exc:
            logger.error("CLI sync failed: %s", exc)
            print(f"Sync failed: {exc}")

    thread = Thread(daemon=True, target=run, name="sync")
    thread.start()
    _watch_for_enter(f"{mode.capitalize()} sync running. Press Enter to exit... ", user_stopped)
    try:
        # Watch the session too: if it ends by itself (no device could start,
        # a fatal capture error) the CLI exits instead of waiting for Enter.
        while thread.is_alive() and not user_stopped.is_set():
            thread.join(0.25)
    except KeyboardInterrupt:
        user_stopped.set()
    finally:
        stop_event.set()
        thread.join(35)  # Bound connection/write/disconnect completion before exit.
    if thread.is_alive():
        logger.error("CLI sync is still waiting for a device operation to finish")
        return 1
    if not user_stopped.is_set():
        if started.is_set():
            print(f"{mode.capitalize()} sync stopped unexpectedly.")
        else:
            print(f"No device could start {mode} sync.")
        return 1
    return 0


def _start_sync(mode: str) -> int:
    """Start monitor or music sync in a thread; block until Enter or it ends."""
    server = None
    try:
        if mode == "monitor":
            from .sync.monitor import capture_supported

            supported, reason = capture_supported()
            if not supported:
                print(reason)
                return 1
        server, candidates = _discover_for_cli()
        selected = _select_cli_targets(mode, candidates)
        if not selected:
            print("No devices found that support screen or music sync over a local connection.")
            return 1
        return _run_cli_sync(
            server,
            selected,
            mode,
            _cli_sync_settings(),
            mapping_provider=_cli_mapping_provider() if mode == "monitor" else None,
        )
    except Exception as e:
        logger.critical("Sync error: %s", e, exc_info=True)
        return 1
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass


def _check_integrations() -> int:
    """Exercise frozen-package dependencies without login or device traffic."""
    import importlib
    try:
        for module in ("requests", "certifi", "keyring", "qrcode", "tuya_sharing", "paho.mqtt.client",
                       "cryptography.hazmat.primitives.serialization.pkcs12", "bleak", "tinytuya"):
            importlib.import_module(module)
        import certifi
        import qrcode
        from .accounts.errors import AccountError
        from .accounts.secrets import CredentialVault
        from .accounts.tuya import TuyaSharingClient
        from .accounts.tuya_mobile import TuyaMobileClient, mobile_signature
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        import cryptography.x509  # noqa: F401 — the APK certificate reader requires this in frozen builds.
        if not Path(certifi.where()).is_file():
            raise RuntimeError("TLS certificate bundle is missing")
        qrcode.make("lumisync-integration-check")
        client = TuyaSharingClient()
        client.close()
        for brand in ("lsc", "tuya"):
            client = TuyaMobileClient(brand)
            client.close()
        if len(mobile_signature({"a": "offline.check"}, "fixture-key")) != 64:
            raise RuntimeError("Mobile request signer failed")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ciphertext = key.public_key().encrypt(b"offline-integration-check", padding.PKCS1v15())
        if key.decrypt(ciphertext, padding.PKCS1v15()) != b"offline-integration-check":
            raise RuntimeError("Mobile password encryption failed")
        try:
            backend = type(CredentialVault._backend()).__name__
        except AccountError:
            backend = "unavailable; session-only connections supported"
        print(f"Vendor integration dependencies OK. Credential store: {backend}")
        return 0
    except Exception as exc:
        print(f"Vendor integration dependency check failed: {type(exc).__name__}")
        return 1


def _check_gui() -> int:
    """Render the actual main window without using personal device settings."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from .gui.diagnostics import check_gui_startup
        check_gui_startup()
        print("GUI startup OK. Main window rendered and closed with isolated settings.")
        return 0
    except Exception:
        logger.error("GUI startup check failed", exc_info=True)
        print("GUI startup check failed. See the LumiSync logs for details.")
        return 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lumisync",
        description="Sync smart lights and pixel panels with your screen and audio. Default: launch GUI.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--monitor", action="store_true", help="Start monitor sync directly (headless).")
    g.add_argument("--music", action="store_true", help="Start music sync directly (headless).")
    g.add_argument("--check-integrations", action="store_true", help="Check vendor integration dependencies offline.")
    g.add_argument("--check-gui", action="store_true", help="Check GUI startup with isolated settings and no device connections.")
    g.add_argument("--quit", action="store_true", help="Ask a running LumiSync instance to quit.")
    # Used by the sign-in entry: start the GUI hidden in the system tray.
    p.add_argument("--minimized", action="store_true", help="Start the GUI in the system tray.")
    return p


def main() -> None:
    _ensure_standard_streams()

    parser = _build_parser()
    args = parser.parse_args()

    if args.monitor:
        sys.exit(_start_sync("monitor"))
    if args.music:
        sys.exit(_start_sync("music"))
    if args.check_integrations:
        sys.exit(_check_integrations())
    if args.check_gui:
        sys.exit(_check_gui())
    if args.quit:
        from .gui.utils.single_instance import request_existing_quit
        sys.exit(0 if request_existing_quit() else 1)

    # Default: GUI. No discovery here — the GUI handles its own.
    sys.exit(_launch_gui())


if __name__ == "__main__":
    main()
