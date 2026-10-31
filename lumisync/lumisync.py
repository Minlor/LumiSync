"""LumiSync entry point.

Default behavior (no args): launch the GUI.
Use ``--monitor`` or ``--music`` for headless sync.
"""

import argparse
import os
import sys
from pathlib import Path
from threading import Event, Thread

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "lumisync"

from . import connection, devices
from .config.sync_settings import SyncSettings
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


def _run_cli_sync(server, selected, mode: str, settings: SyncSettings) -> int:
    from .sync.session import SyncSession

    stop_event = Event()
    failures = []

    def report_error(message: str) -> None:
        failures.append(message)
        print(message)

    session = SyncSession(
        mode,
        selected,
        settings,
        server=server,
        stop_event=stop_event,
        on_error=report_error,
    )

    def run():
        try:
            session.run()
        except Exception as exc:
            failures.append(exc)
            logger.error("CLI sync failed: %s", exc)
            print(f"Sync failed: {exc}")

    thread = Thread(daemon=True, target=run, name="sync")
    thread.start()
    try:
        input(f"{mode.capitalize()} sync running. Press Enter to exit...")
    finally:
        stop_event.set()
        thread.join(35)  # Bound connection/write/disconnect completion before exit.
    if thread.is_alive():
        logger.error("CLI sync is still waiting for a device operation to finish")
        return 1
    return 1 if failures else 0


def _start_sync(mode: str) -> int:
    """Start monitor or music sync in a thread; block on Enter."""
    server = None
    try:
        server, selected = _discover_for_cli()
        if not selected:
            print("No devices found.")
            return 1
        return _run_cli_sync(server, selected, mode, _cli_sync_settings())
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

    # Default: GUI. No discovery here — the GUI handles its own.
    sys.exit(_launch_gui())


if __name__ == "__main__":
    main()
