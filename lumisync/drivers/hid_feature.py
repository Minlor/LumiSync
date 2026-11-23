"""USB HID reports, the small part of HID that lighting controllers use.

This layer knows nothing about any particular device. It lists the HID
interfaces of one USB vendor/product pair, opens one, and exchanges reports
with it: feature reports through :class:`HidDevice` (``gigabyte_fusion``),
or output reports out and input reports back, with a read timeout, through
:class:`HidStream` (``logitech_hidpp``). Drivers build the protocol on top.

Two platforms are supported, both with the standard library only:

- **Windows** calls the system ``hid.dll``, ``cfgmgr32.dll`` and
  ``kernel32.dll`` through :mod:`ctypes`. Each top-level collection is its
  own interface path. Every Win32 failure raises :class:`OSError` with the
  Win32 error code in ``winerror`` (and ``errno`` mapped from it by Python).
- **Linux** uses the kernel's ``hidraw`` interface. Devices are listed from
  sysfs (``/sys/class/hidraw/hidrawN/device/uevent`` and
  ``report_descriptor``), which needs no permission; the report descriptor
  is parsed here to find the top-level collections and their report
  lengths. One ``/dev/hidrawN`` node carries every collection of the USB
  interface, so all of them share that path. Feature reports go through the
  ``HIDIOCSFEATURE``/``HIDIOCGFEATURE`` ioctls; output reports are written
  and input reports read on the node itself. Failures raise the
  :class:`OSError` the system call raised (:class:`PermissionError` when the
  node is root-only, which is the default without a udev rule).

On any other platform :func:`enumerate` returns an empty list and
:meth:`HidDevice.open` raises :class:`HidUnsupportedPlatform`. Importing this
module never touches a device, loads a DLL or imports :mod:`fcntl` off Linux;
device access happens on first use.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "HidCollection",
    "HidDeviceInfo",
    "HidDevice",
    "HidStream",
    "HidUnsupportedPlatform",
    "enumerate",
    "hidiocgfeature",
    "hidiocsfeature",
    "parse_hid_id",
    "parse_report_descriptor",
    "supported",
]


class HidUnsupportedPlatform(OSError):
    """HID feature-report access is not implemented on this platform."""


@dataclass(frozen=True)
class HidDeviceInfo:
    """One top-level HID collection of a USB device.

    A composite device exposes one interface path per top-level collection,
    each with its own usage and report lengths, so a driver picks the
    collection it speaks to by ``usage_page``/``usage``.
    """

    path: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    feature_report_length: int
    # Longest input/output report in bytes, report-ID byte included (0: none).
    input_report_length: int = 0
    output_report_length: int = 0


def supported() -> bool:
    """Whether this platform has a HID implementation."""
    return sys.platform in ("win32", "linux")


def _unsupported() -> HidUnsupportedPlatform:
    return HidUnsupportedPlatform(
        f"USB HID lighting control is not supported on this platform ({sys.platform}); "
        "it is available on Windows and Linux."
    )


# ------------------------------------------------- report descriptor parser
#
# Platform-neutral so it is tested everywhere. Windows parses descriptors
# itself (HidP_GetCaps); on Linux the raw descriptor comes from sysfs and is
# parsed here, reporting lengths the way Windows does.


@dataclass(frozen=True)
class HidCollection:
    """One top-level application collection of a report descriptor."""

    usage_page: int
    usage: int
    # Longest feature report of the collection in bytes, including the
    # report-ID byte when that report has an ID; 0 if it has no feature report.
    feature_report_length: int
    # The same for input and output reports.
    input_report_length: int = 0
    output_report_length: int = 0


# Item types (bits 2-3 of the prefix byte).
_ITEM_MAIN = 0
_ITEM_GLOBAL = 1
_ITEM_LOCAL = 2
# Main item tags.
_MAIN_INPUT = 0x8
_MAIN_OUTPUT = 0x9
_MAIN_FEATURE = 0xB
_MAIN_COLLECTION = 0xA
_MAIN_END_COLLECTION = 0xC
# Global item tags.
_GLOBAL_USAGE_PAGE = 0x0
_GLOBAL_REPORT_SIZE = 0x7
_GLOBAL_REPORT_ID = 0x8
_GLOBAL_REPORT_COUNT = 0x9
_GLOBAL_PUSH = 0xA
_GLOBAL_POP = 0xB
# Local item tags.
_LOCAL_USAGE = 0x0
_COLLECTION_APPLICATION = 0x01
_LONG_ITEM_PREFIX = 0xFE
_SHORT_ITEM_SIZES = (0, 1, 2, 4)


def parse_report_descriptor(descriptor: bytes) -> List[HidCollection]:
    """The top-level application collections of a HID report descriptor.

    Handles short items (0, 1, 2 or 4 data bytes) and skips long items. Only
    the state that decides a collection's usage and its report lengths
    (feature, input, output) is tracked: Usage Page, Report Size, Report Count and Report ID
    (with Push/Pop), and the local Usage, including the 4-byte form that
    carries its own usage page. Never raises: a truncated or malformed
    descriptor yields the collections completed before the problem.
    """
    results: List[HidCollection] = []
    try:
        _parse_into(bytes(descriptor), results)
    except Exception:  # Defensive: garbage in must never take discovery down.
        pass
    return results


def _longest_report(bits_by_id: Dict[int, int]) -> int:
    """Bytes of the longest report, plus the ID byte when the report has an ID."""
    length = 0
    for report_id, bits in bits_by_id.items():
        length = max(length, (bits + 7) // 8 + (1 if report_id else 0))
    return length


def _parse_into(data: bytes, results: List[HidCollection]) -> None:
    # Global state: [usage_page, report_size, report_count, report_id].
    state = [0, 0, 0, 0]
    stack: List[List[int]] = []
    usages: List[Tuple[Optional[int], int]] = []  # (explicit page or None, usage)
    depth = 0
    top_page = top_usage = 0
    in_application = False
    # Main item tag -> {report id -> bits}, for the current collection.
    report_bits: Dict[int, Dict[int, int]] = {}

    offset = 0
    end = len(data)
    while offset < end:
        prefix = data[offset]
        if prefix == _LONG_ITEM_PREFIX:
            if offset + 3 > end:
                return
            offset += 3 + data[offset + 1]
            if offset > end:
                return
            continue
        size = _SHORT_ITEM_SIZES[prefix & 0x03]
        kind = (prefix >> 2) & 0x03
        tag = (prefix >> 4) & 0x0F
        if offset + 1 + size > end:
            return  # Truncated item.
        value = int.from_bytes(data[offset + 1:offset + 1 + size], "little")
        offset += 1 + size

        if kind == _ITEM_MAIN:
            if tag == _MAIN_COLLECTION:
                if depth == 0:
                    in_application = value == _COLLECTION_APPLICATION
                    page, usage = usages[0] if usages else (None, 0)
                    top_page = state[0] if page is None else page
                    top_usage = usage
                    report_bits = {}
                depth += 1
            elif tag == _MAIN_END_COLLECTION:
                if depth == 0:
                    return  # Unbalanced: stop here.
                depth -= 1
                if depth == 0 and in_application:
                    results.append(HidCollection(
                        top_page,
                        top_usage,
                        _longest_report(report_bits.get(_MAIN_FEATURE, {})),
                        _longest_report(report_bits.get(_MAIN_INPUT, {})),
                        _longest_report(report_bits.get(_MAIN_OUTPUT, {})),
                    ))
                    in_application = False
            elif tag in (_MAIN_FEATURE, _MAIN_INPUT, _MAIN_OUTPUT) and depth > 0:
                report_id = state[3]
                bits = report_bits.setdefault(tag, {})
                bits[report_id] = bits.get(report_id, 0) + state[1] * state[2]
            # Every main item ends the local state.
            usages = []
        elif kind == _ITEM_GLOBAL:
            if tag == _GLOBAL_USAGE_PAGE:
                state[0] = value
            elif tag == _GLOBAL_REPORT_SIZE:
                state[1] = value
            elif tag == _GLOBAL_REPORT_COUNT:
                state[2] = value
            elif tag == _GLOBAL_REPORT_ID:
                state[3] = value
            elif tag == _GLOBAL_PUSH:
                stack.append(list(state))
            elif tag == _GLOBAL_POP:
                if not stack:
                    return  # Pop without Push: malformed.
                state = stack.pop()
        elif kind == _ITEM_LOCAL:
            if tag == _LOCAL_USAGE:
                if size == 4:
                    usages.append((value >> 16, value & 0xFFFF))
                else:
                    usages.append((None, value))


# ------------------------------------------------------------ Linux helpers
#
# Platform-neutral pieces of the hidraw backend, so they are tested on every
# OS. Only the system calls live under the Linux platform guard below.

# Patched in tests to point at a fake tree.
_SYSFS_HIDRAW_ROOT = "/sys/class/hidraw"
_DEV_ROOT = "/dev"

_HID_ID_PATTERN = re.compile(r"^HID_ID=([0-9A-Fa-f]{1,8}):([0-9A-Fa-f]{1,8}):([0-9A-Fa-f]{1,8})$")


def parse_hid_id(uevent: str) -> Optional[Tuple[int, int, int]]:
    """``(bus, vendor, product)`` from the ``HID_ID=`` line of a uevent file.

    The line reads ``HID_ID=BBBB:VVVVVVVV:PPPPPPPP`` (hex). Returns None if
    the text has no well-formed ``HID_ID`` line.
    """
    for line in uevent.splitlines():
        match = _HID_ID_PATTERN.match(line.strip())
        if match:
            bus, vendor, product = (int(part, 16) for part in match.groups())
            return bus, vendor, product
    return None


# The generic Linux ioctl number layout (asm-generic/ioctl.h): nr in bits
# 0-7, type in bits 8-15, size in bits 16-29, direction in bits 30-31, with
# write = 1 and read = 2. x86, ARM and RISC-V all use this layout, which
# covers every platform LumiSync ships for (it differs on e.g. PowerPC/MIPS).
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = 8
_IOC_SIZESHIFT = 16
_IOC_DIRSHIFT = 30
_IOC_SIZEMASK = (1 << 14) - 1
_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, kind: str, number: int, size: int) -> int:
    if not 0 <= size <= _IOC_SIZEMASK:
        raise ValueError(f"An ioctl buffer of {size} bytes does not fit the size field.")
    return (
        (direction << _IOC_DIRSHIFT)
        | (ord(kind) << _IOC_TYPESHIFT)
        | (number << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def hidiocsfeature(length: int) -> int:
    """``HIDIOCSFEATURE(len)``: send a feature report of ``length`` bytes."""
    return _ioc(_IOC_WRITE | _IOC_READ, "H", 0x06, length)


def hidiocgfeature(length: int) -> int:
    """``HIDIOCGFEATURE(len)``: read a feature report of ``length`` bytes."""
    return _ioc(_IOC_WRITE | _IOC_READ, "H", 0x07, length)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _hidraw_sort_key(name: str) -> Tuple[int, str]:
    suffix = name[len("hidraw"):]
    return (int(suffix) if suffix.isdigit() else 1 << 30, name)


def _scan_hidraw(vendor_id: int, product_id: int) -> List[HidDeviceInfo]:
    """The hidraw collections of one vendor/product pair, from sysfs only.

    Nothing under ``/dev`` is opened. Each matching node yields one entry per
    top-level collection that has any report (feature, input or output), all
    with the node's path, like Windows lists every collection. A node whose
    files are missing or unreadable is skipped.
    """
    root = _SYSFS_HIDRAW_ROOT
    try:
        names = [name for name in os.listdir(root) if name.startswith("hidraw")]
    except OSError:
        return []
    results: List[HidDeviceInfo] = []
    for name in sorted(names, key=_hidraw_sort_key):
        device_dir = os.path.join(root, name, "device")
        uevent = _read_text(os.path.join(device_dir, "uevent"))
        ids = parse_hid_id(uevent) if uevent is not None else None
        if ids is None or (ids[1], ids[2]) != (vendor_id, product_id):
            continue
        descriptor = _read_bytes(os.path.join(device_dir, "report_descriptor"))
        if not descriptor:
            continue
        path = posixpath.join(_DEV_ROOT, name)
        for collection in parse_report_descriptor(descriptor):
            if max(
                collection.feature_report_length,
                collection.input_report_length,
                collection.output_report_length,
            ) <= 0:
                continue
            results.append(HidDeviceInfo(
                path=path,
                vendor_id=vendor_id,
                product_id=product_id,
                usage_page=collection.usage_page,
                usage=collection.usage,
                feature_report_length=collection.feature_report_length,
                input_report_length=collection.input_report_length,
                output_report_length=collection.output_report_length,
            ))
    return results


def _path_matches(path: str, vendor_id: int, product_id: int) -> bool:
    """Whether an interface path belongs to ``vendor_id``/``product_id``.

    Windows HID paths embed ``vid_XXXX&pid_XXXX``. Matching on the text means
    devices of other vendors (keyboards, mice) are never opened at all.
    """
    return f"vid_{vendor_id:04x}&pid_{product_id:04x}" in path.lower()


def enumerate(vendor_id: int, product_id: int) -> List[HidDeviceInfo]:  # noqa: A001 - module API
    """List the present HID collections of one USB vendor/product pair.

    On Windows only interfaces whose path names that pair are opened, and
    only to read their attributes and capabilities (no report is exchanged).
    On Linux nothing is opened: everything comes from sysfs. Returns an
    empty list on platforms without a HID implementation.
    """
    if sys.platform == "linux":
        return _scan_hidraw(vendor_id, product_id)
    if sys.platform != "win32":
        return []
    api = _windows_api()
    results: List[HidDeviceInfo] = []
    for path in api.interface_paths():
        if not _path_matches(path, vendor_id, product_id):
            continue
        info = api.describe(path)
        if info is None:
            continue
        if info.vendor_id == vendor_id and info.product_id == product_id:
            results.append(info)
    return results


class HidDevice:
    """An open handle to one HID collection, for feature reports.

    Not thread-safe by itself: callers that share a device between threads
    serialise access (the lighting drivers hold a per-controller lock).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._handle: Optional[int] = None

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def open(self) -> None:
        """Open the collection for reading and writing feature reports."""
        if self._handle is not None:
            return
        self._handle = _platform_api().open(self.path, read_write=True)

    def send_feature_report(self, data: bytes) -> None:
        """Send one feature report. ``data[0]`` is the report ID."""
        api = _platform_api()
        handle = self._require_handle()
        api.set_feature(handle, bytes(data))

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        """Read one feature report of ``length`` bytes (report ID included)."""
        api = _platform_api()
        handle = self._require_handle()
        return api.get_feature(handle, int(report_id), int(length))

    def close(self) -> None:
        """Release the handle. Safe to call more than once."""
        handle, self._handle = self._handle, None
        if handle is None or not supported():
            return
        _platform_api().close(handle)

    def _require_handle(self) -> int:
        if self._handle is None:
            raise OSError("The HID device is not open.")
        return self._handle

    def __enter__(self) -> "HidDevice":
        self.open()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class HidStream:
    """An open handle to one HID collection, for output and input reports.

    Protocols that send a request as an output report and get the answer
    back as an input report (Logitech HID++) use this instead of
    :class:`HidDevice`. The handle is shared with other programs (the
    collection is opened with shared read and write access), so every input
    report the device sends arrives here too, not only the replies to this
    handle's requests: callers match replies themselves.

    Not thread-safe by itself; callers serialise access.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._handle: Any = None

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def open(self) -> None:
        """Open the collection for writing output and reading input reports."""
        if self._handle is not None:
            return
        self._handle = _platform_api().open_stream(self.path)

    def write(self, data: bytes, timeout: float = 1.0) -> None:
        """Send one output report. ``data[0]`` is the report ID.

        Raises :class:`TimeoutError` (an :class:`OSError`) if the report is
        not taken within ``timeout`` seconds, and :class:`OSError` on failure.
        """
        api = _platform_api()
        handle = self._require_handle()
        api.write_report(handle, bytes(data), max(0.0, float(timeout)))

    def read(self, size: int, timeout: float) -> Optional[bytes]:
        """The next input report (report ID first), or None after ``timeout`` seconds.

        ``size`` is the largest report expected; a timeout of 0 only returns
        a report that is already waiting.
        """
        api = _platform_api()
        handle = self._require_handle()
        return api.read_report(handle, int(size), max(0.0, float(timeout)))

    def close(self) -> None:
        """Release the handle. Safe to call more than once."""
        handle, self._handle = self._handle, None
        if handle is None or not supported():
            return
        _platform_api().close_stream(handle)

    def _require_handle(self) -> Any:
        if self._handle is None:
            raise OSError("The HID device is not open.")
        return self._handle

    def __enter__(self) -> "HidStream":
        self.open()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


_api_lock = threading.Lock()
_api_instance: Any = None


def _windows_api() -> Any:
    """The lazily built ctypes bindings (Windows only)."""
    global _api_instance
    with _api_lock:
        if _api_instance is None:
            _api_instance = _WindowsHid()
        return _api_instance


def _platform_api() -> Any:
    """This platform's backend, or :class:`HidUnsupportedPlatform`.

    Both backends offer ``open(path, read_write=)``, ``set_feature``,
    ``get_feature`` and ``close`` on an integer handle, and ``open_stream``,
    ``write_report``, ``read_report`` and ``close_stream`` on a stream handle.
    """
    if sys.platform == "win32":
        return _windows_api()
    if sys.platform == "linux":
        return _LinuxHid()
    raise _unsupported()


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _CR_SUCCESS = 0x00000000
    _CR_BUFFER_SMALL = 0x0000001A
    _CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0x00000000
    _HIDP_STATUS_SUCCESS = 0x00110000
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _ERROR_OPERATION_ABORTED = 995
    _ERROR_IO_PENDING = 997
    _WAIT_OBJECT_0 = 0

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    @dataclass(frozen=True)
    class _WinStream:
        """A collection opened for overlapped IO, and the event its IO signals."""

        handle: int
        event: int

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _HIDD_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Size", wintypes.ULONG),
            ("VendorID", wintypes.USHORT),
            ("ProductID", wintypes.USHORT),
            ("VersionNumber", wintypes.USHORT),
        ]

    class _HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT),
        ]

    class _WindowsHid:
        """ctypes bindings with explicit argument and return types.

        Every function gets ``argtypes``/``restype``: the ctypes default
        return type is a C ``int``, which truncates 64-bit handles.
        """

        def __init__(self) -> None:
            hid = ctypes.WinDLL("hid", use_last_error=True)
            cfgmgr = ctypes.WinDLL("cfgmgr32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)

            self._get_hid_guid = hid.HidD_GetHidGuid
            self._get_hid_guid.argtypes = [ctypes.POINTER(_GUID)]
            self._get_hid_guid.restype = None

            self._get_attributes = hid.HidD_GetAttributes
            self._get_attributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
            self._get_attributes.restype = wintypes.BOOLEAN

            self._get_preparsed = hid.HidD_GetPreparsedData
            self._get_preparsed.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
            self._get_preparsed.restype = wintypes.BOOLEAN

            self._free_preparsed = hid.HidD_FreePreparsedData
            self._free_preparsed.argtypes = [ctypes.c_void_p]
            self._free_preparsed.restype = wintypes.BOOLEAN

            self._get_caps = hid.HidP_GetCaps
            self._get_caps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
            self._get_caps.restype = wintypes.LONG

            self._set_feature = hid.HidD_SetFeature
            self._set_feature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
            self._set_feature.restype = wintypes.BOOLEAN

            self._get_feature = hid.HidD_GetFeature
            self._get_feature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
            self._get_feature.restype = wintypes.BOOLEAN

            self._list_size = cfgmgr.CM_Get_Device_Interface_List_SizeW
            self._list_size.argtypes = [
                ctypes.POINTER(wintypes.ULONG),
                ctypes.POINTER(_GUID),
                wintypes.LPCWSTR,
                wintypes.ULONG,
            ]
            self._list_size.restype = wintypes.DWORD

            self._list = cfgmgr.CM_Get_Device_Interface_ListW
            self._list.argtypes = [
                ctypes.POINTER(_GUID),
                wintypes.LPCWSTR,
                ctypes.c_void_p,
                wintypes.ULONG,
                wintypes.ULONG,
            ]
            self._list.restype = wintypes.DWORD

            self._create_file = kernel.CreateFileW
            self._create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            self._create_file.restype = wintypes.HANDLE

            self._close_handle = kernel.CloseHandle
            self._close_handle.argtypes = [wintypes.HANDLE]
            self._close_handle.restype = wintypes.BOOL

            # Output/input reports: overlapped WriteFile/ReadFile, so a read
            # can give up after a timeout instead of blocking for ever.
            self._create_event = kernel.CreateEventW
            self._create_event.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            self._create_event.restype = wintypes.HANDLE

            self._reset_event = kernel.ResetEvent
            self._reset_event.argtypes = [wintypes.HANDLE]
            self._reset_event.restype = wintypes.BOOL

            overlapped_io = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(_OVERLAPPED),
            ]
            self._write_file = kernel.WriteFile
            self._write_file.argtypes = overlapped_io
            self._write_file.restype = wintypes.BOOL

            self._read_file = kernel.ReadFile
            self._read_file.argtypes = overlapped_io
            self._read_file.restype = wintypes.BOOL

            self._overlapped_result = kernel.GetOverlappedResult
            self._overlapped_result.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_OVERLAPPED),
                ctypes.POINTER(wintypes.DWORD),
                wintypes.BOOL,
            ]
            self._overlapped_result.restype = wintypes.BOOL

            self._wait = kernel.WaitForSingleObject
            self._wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self._wait.restype = wintypes.DWORD

            self._cancel_io = kernel.CancelIo
            self._cancel_io.argtypes = [wintypes.HANDLE]
            self._cancel_io.restype = wintypes.BOOL

        @staticmethod
        def _error(what: str, code: Optional[int] = None) -> OSError:
            code = ctypes.get_last_error() if code is None else code
            return ctypes.WinError(code, f"{what} failed: {ctypes.FormatError(code).strip()}")

        def interface_paths(self) -> List[str]:
            """Interface paths of every present HID collection."""
            guid = _GUID()
            self._get_hid_guid(ctypes.byref(guid))
            for _attempt in range(5):
                size = wintypes.ULONG(0)
                status = self._list_size(
                    ctypes.byref(size), ctypes.byref(guid), None,
                    _CM_GET_DEVICE_INTERFACE_LIST_PRESENT,
                )
                if status != _CR_SUCCESS:
                    raise OSError(f"Listing HID interfaces failed (CONFIGRET 0x{status:X}).")
                if size.value <= 1:
                    return []
                buffer = ctypes.create_unicode_buffer(size.value)
                status = self._list(
                    ctypes.byref(guid), None, ctypes.cast(buffer, ctypes.c_void_p),
                    size.value, _CM_GET_DEVICE_INTERFACE_LIST_PRESENT,
                )
                if status == _CR_BUFFER_SMALL:
                    continue  # A device arrived between the two calls.
                if status != _CR_SUCCESS:
                    raise OSError(f"Listing HID interfaces failed (CONFIGRET 0x{status:X}).")
                # A multi-sz list: NUL-separated strings ending in a double NUL.
                text = ctypes.wstring_at(buffer, size.value)
                return [item for item in text.split("\0") if item]
            raise OSError("Listing HID interfaces failed: the device list kept changing.")

        def open(self, path: str, *, read_write: bool) -> int:
            access = (_GENERIC_READ | _GENERIC_WRITE) if read_write else 0
            handle = self._create_file(
                path, access, _FILE_SHARE_READ | _FILE_SHARE_WRITE, None,
                _OPEN_EXISTING, 0, None,
            )
            if handle is None or handle == _INVALID_HANDLE_VALUE:
                raise self._error("Opening the HID device")
            return int(handle)

        def close(self, handle: int) -> None:
            self._close_handle(handle)

        def describe(self, path: str) -> Optional[HidDeviceInfo]:
            """Attributes and top-level capabilities, or None if unreadable.

            Opened with no access rights: that is enough for the queries and
            works even while another program holds the collection.
            """
            try:
                handle = self.open(path, read_write=False)
            except OSError:
                return None
            try:
                attributes = _HIDD_ATTRIBUTES()
                attributes.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
                if not self._get_attributes(handle, ctypes.byref(attributes)):
                    return None
                preparsed = ctypes.c_void_p()
                if not self._get_preparsed(handle, ctypes.byref(preparsed)):
                    return None
                try:
                    caps = _HIDP_CAPS()
                    if self._get_caps(preparsed, ctypes.byref(caps)) != _HIDP_STATUS_SUCCESS:
                        return None
                finally:
                    self._free_preparsed(preparsed)
                return HidDeviceInfo(
                    path=path,
                    vendor_id=int(attributes.VendorID),
                    product_id=int(attributes.ProductID),
                    usage_page=int(caps.UsagePage),
                    usage=int(caps.Usage),
                    feature_report_length=int(caps.FeatureReportByteLength),
                    input_report_length=int(caps.InputReportByteLength),
                    output_report_length=int(caps.OutputReportByteLength),
                )
            finally:
                self.close(handle)

        def open_stream(self, path: str) -> "_WinStream":
            """Open a collection for overlapped IO, shared with other programs."""
            handle = self._create_file(
                path, _GENERIC_READ | _GENERIC_WRITE, _FILE_SHARE_READ | _FILE_SHARE_WRITE, None,
                _OPEN_EXISTING, _FILE_FLAG_OVERLAPPED, None,
            )
            if handle is None or handle == _INVALID_HANDLE_VALUE:
                raise self._error("Opening the HID device")
            # Manual reset, so a completion is never missed between calls.
            event = self._create_event(None, True, False, None)
            if not event:
                error = self._error("Creating an IO event")
                self._close_handle(handle)
                raise error
            return _WinStream(int(handle), int(event))

        def close_stream(self, stream: "_WinStream") -> None:
            self._close_handle(stream.handle)
            self._close_handle(stream.event)

        def _overlapped(self, stream: "_WinStream", start: Any, buffer: Any, length: int,
                        timeout: float, what: str) -> Optional[int]:
            """Run one overlapped read or write; bytes moved, or None on timeout.

            On a timeout the request is cancelled and its completion awaited
            before returning: the buffer and OVERLAPPED must outlive it. A
            request that completed just before the cancel still counts.
            """
            overlapped = _OVERLAPPED()
            overlapped.hEvent = stream.event
            self._reset_event(stream.event)
            done = wintypes.DWORD(0)
            pointer = ctypes.cast(buffer, ctypes.c_void_p)
            if not start(stream.handle, pointer, length, None, ctypes.byref(overlapped)):
                code = ctypes.get_last_error()
                if code != _ERROR_IO_PENDING:
                    raise self._error(what, code)
                milliseconds = max(0, int(timeout * 1000 + 0.999))
                if self._wait(stream.event, milliseconds) != _WAIT_OBJECT_0:
                    self._cancel_io(stream.handle)
                    if self._overlapped_result(stream.handle, ctypes.byref(overlapped), ctypes.byref(done), True):
                        return int(done.value)
                    code = ctypes.get_last_error()
                    if code == _ERROR_OPERATION_ABORTED:
                        return None
                    raise self._error(what, code)
            if not self._overlapped_result(stream.handle, ctypes.byref(overlapped), ctypes.byref(done), True):
                raise self._error(what)
            return int(done.value)

        def write_report(self, stream: "_WinStream", data: bytes, timeout: float) -> None:
            # Windows wants exactly the collection's output report length.
            buffer = ctypes.create_string_buffer(data, len(data))
            written = self._overlapped(stream, self._write_file, buffer, len(data), timeout,
                                       "Sending a HID output report")
            if written is None:
                raise TimeoutError("Sending a HID output report timed out.")

        def read_report(self, stream: "_WinStream", size: int, timeout: float) -> Optional[bytes]:
            buffer = ctypes.create_string_buffer(size)
            count = self._overlapped(stream, self._read_file, buffer, size, timeout,
                                     "Reading a HID input report")
            if not count:
                return None
            return bytes(buffer.raw[:count])

        def set_feature(self, handle: int, data: bytes) -> None:
            buffer = ctypes.create_string_buffer(data, len(data))
            if not self._set_feature(handle, ctypes.cast(buffer, ctypes.c_void_p), len(data)):
                raise self._error("Sending a HID feature report")

        def get_feature(self, handle: int, report_id: int, length: int) -> bytes:
            # The caller names the report it wants in the first byte.
            buffer = ctypes.create_string_buffer(bytes([report_id & 0xFF]) + bytes(length - 1), length)
            if not self._get_feature(handle, ctypes.cast(buffer, ctypes.c_void_p), length):
                raise self._error("Reading a HID feature report")
            return bytes(buffer.raw[:length])

else:  # pragma: no cover - non-Windows platforms never build the bindings

    class _WindowsHid:  # noqa: D401 - placeholder so the name always exists
        def __init__(self) -> None:
            raise _unsupported()


if sys.platform == "linux":
    import fcntl
    import select

    class _LinuxHid:
        """hidraw system calls on a file descriptor.

        Errors are the :class:`OSError` the call raised; ``EACCES`` arrives
        as :class:`PermissionError` (``/dev/hidraw*`` is root-only unless a
        udev rule grants access).
        """

        def open(self, path: str, *, read_write: bool = True) -> int:
            flags = (os.O_RDWR if read_write else os.O_RDONLY) | os.O_CLOEXEC
            return os.open(path, flags)

        def close(self, handle: int) -> None:
            os.close(handle)

        def set_feature(self, handle: int, data: bytes) -> None:
            # A mutable buffer: the kernel copies the report from it.
            buffer = bytearray(data)
            fcntl.ioctl(handle, hidiocsfeature(len(buffer)), buffer, True)

        def get_feature(self, handle: int, report_id: int, length: int) -> bytes:
            # The first byte names the report; the kernel fills the rest.
            buffer = bytearray(length)
            buffer[0] = report_id & 0xFF
            fcntl.ioctl(handle, hidiocgfeature(length), buffer, True)
            return bytes(buffer)

        # Output and input reports go through the node itself: write() sends
        # one output report (report ID first), read() returns one input
        # report of any collection on the node (report ID first when the
        # device numbers its reports).

        def open_stream(self, path: str) -> int:
            return self.open(path, read_write=True)

        def close_stream(self, handle: int) -> None:
            os.close(handle)

        def write_report(self, handle: int, data: bytes, timeout: float) -> None:
            # hidraw writes are synchronous with a kernel-side USB timeout of
            # their own, so ``timeout`` is not needed here.
            written = os.write(handle, data)
            if written != len(data):
                raise OSError(f"Sent {written} of {len(data)} bytes of a HID output report.")

        def read_report(self, handle: int, size: int, timeout: float) -> Optional[bytes]:
            ready, _, _ = select.select([handle], [], [], timeout)
            if not ready:
                return None
            return os.read(handle, size)

else:  # pragma: no cover - only Linux builds the hidraw backend

    class _LinuxHid:  # noqa: D401 - placeholder so the name always exists
        def __init__(self) -> None:
            raise _unsupported()
