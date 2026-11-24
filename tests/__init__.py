"""Test package bootstrap.

Setting this before any test module is imported keeps logs, including those
from subprocesses spawned by the GUI packaging tests, out of the developer's
real ``~/.lumisync/logs`` folder. It also gives the suite a per-test watchdog:
one hung test fails the run with a stack dump instead of eating the whole CI
job budget.
"""

import faulthandler
import os
import sys
import threading
import unittest

os.environ.setdefault("LUMISYNC_TESTING", "1")

# Never touch the developer's real preferences. On Windows the app's QSettings
# store is the registry, and a source checkout keeps settings.json in the
# working directory; tests used to rewrite both (sync targets, the device list).
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_ISOLATED_PROFILE = tempfile.mkdtemp(prefix="lumisync-tests-")
atexit.register(shutil.rmtree, _ISOLATED_PROFILE, True)
os.environ["LUMISYNC_SETTINGS_INI"] = os.path.join(_ISOLATED_PROFILE, "LumiSync.ini")
os.environ["LUMISYNC_SETTINGS_FILE"] = os.path.join(_ISOLATED_PROFILE, "settings.json")

# Never touch the developer's real credential store (Windows Credential
# Manager, macOS Keychain, Secret Service): every account saved there under
# "LumiSync" is the developer's own. keyring's "fail" backend is not a secure
# store, so the vault reports "unavailable" unless a test supplies a fake
# backend (patch ``CredentialVault._backend``).
try:
    import keyring as _keyring  # noqa: E402
    from keyring.backends import fail as _keyring_fail  # noqa: E402
except ImportError:  # A bare Python without the app's dependencies.
    pass
else:
    _keyring.set_keyring(_keyring_fail.Keyring())

# Widgets render off screen. On a desktop session every test that shows a
# widget would otherwise flash a real window over whatever the developer is
# doing. LUMISYNC_TEST_SHOW_WINDOWS=1 opts back in for debugging.
if os.environ.get("LUMISYNC_TEST_SHOW_WINDOWS") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TEST_TIMEOUT = max(5.0, float(os.environ.get("LUMISYNC_TEST_TIMEOUT", "120")))
_original_run = unittest.TestCase.run


def _expire(test) -> None:
    sys.stderr.write(f"\nTest timed out after {_TEST_TIMEOUT:g}s: {test.id()}\n")
    sys.stderr.flush()
    faulthandler.dump_traceback()
    # The test owns the process; leaving it alive would hang the CI job.
    os._exit(1)


def _run_with_timeout(self, result=None):
    watchdog = threading.Timer(_TEST_TIMEOUT, _expire, args=(self,))
    watchdog.daemon = True
    watchdog.start()
    try:
        return _original_run(self, result)
    finally:
        watchdog.cancel()


unittest.TestCase.run = _run_with_timeout


# No test may find or open the developer's real Logitech mouse: by default
# the HID++ driver's HID layer lists nothing and opens nothing. Tests of the
# driver patch ``logitech_hidpp._hid`` with their own scripted fake.
class _NoHidpp:
    """An empty HID layer for the Logitech driver."""

    @staticmethod
    def enumerate(_vendor_id, _product_id):
        return []

    @staticmethod
    def HidStream(path):  # noqa: N802 - mirrors hid_feature.HidStream
        raise AssertionError(f"A test opened a real HID device ({path}) through the Logitech driver.")


try:
    from lumisync.drivers import logitech_hidpp as _logitech_hidpp  # noqa: E402
except ImportError:  # A bare Python without the app's dependencies: nothing to isolate.
    pass
else:
    _logitech_hidpp._hid = _NoHidpp()


# The same for the Lorgar keyboard driver: tests of it patch
# ``lorgar_keyboard._hid`` with their own fake keyboard.
class _NoLorgar:
    """An empty HID layer for the Lorgar keyboard driver."""

    @staticmethod
    def enumerate(_vendor_id, _product_id):
        return []

    @staticmethod
    def HidStream(path):  # noqa: N802 - mirrors hid_feature.HidStream
        raise AssertionError(f"A test opened a real HID device ({path}) through the Lorgar driver.")


try:
    from lumisync.drivers import lorgar_keyboard as _lorgar_keyboard  # noqa: E402
except ImportError:
    pass
else:
    _lorgar_keyboard._hid = _NoLorgar()


# The same for the ASUS laptop driver: by default it lists nothing and opens
# nothing. Tests of it patch ``asus_laptop._acpi`` and system info.
class _NoAsus:
    """An empty ACPI layer for the ASUS laptop driver."""

    @staticmethod
    def open_device():
        raise AssertionError("A test opened a real ACPI device through the ASUS laptop driver.")

    @staticmethod
    def send_control(_handle, _packet):
        raise AssertionError("A test sent an ACPI control call through the ASUS laptop driver.")

    @staticmethod
    def query_status(_handle, _dev_id):
        raise AssertionError("A test sent an ACPI status query through the ASUS laptop driver.")

    @staticmethod
    def close_device(_handle):
        pass


try:
    from lumisync.drivers import asus_laptop as _asus_laptop  # noqa: E402
except ImportError:
    pass
else:
    _asus_laptop._acpi = _NoAsus()
    _asus_laptop._get_system_info = lambda: (False, "")

