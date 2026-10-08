"""Resolve Tuya OEM client configuration, with optional app-package overrides.

The supported apps have built-in configuration. Android packages, tooling and
native libraries are not required at runtime. An explicitly imported override
stays in the credential vault. Account passwords are unrelated to this data.
"""

from __future__ import annotations

from contextlib import contextmanager
from fractions import Fraction
import hashlib
import hmac
from pathlib import Path
import re
import struct
import tempfile
import zipfile

from .errors import AccountError
from .mobile_app_profiles import builtin_profile
from .secrets import vault

PACKAGES = {"lsc": "com.lscsmartconnection.smart", "tuya": "com.tuya.smart"}
_MAX_APK = 512 * 1024 * 1024


def _u32(data, pos):
    return struct.unpack_from("<I", data, pos)[0]


def _uleb(data, pos):
    value = 0
    for shift in range(0, 35, 7):
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError("Invalid DEX integer")


def _dex_value(data, pos, strings):
    tag = data[pos]
    pos += 1
    kind, size = tag & 31, (tag >> 5) + 1
    if kind in (0x1e, 0x1f):
        return None, pos
    if kind == 0x1c:
        count, pos = _uleb(data, pos)
        if count > 256:
            raise ValueError("Invalid DEX array")
        for _ in range(count):
            _, pos = _dex_value(data, pos, strings)
        return None, pos
    if kind == 0x1d:
        raise ValueError("Unsupported DEX annotation")
    raw = int.from_bytes(data[pos:pos + size], "little")
    return (strings[raw] if kind == 0x17 else raw), pos + size


def _dex_config(data: bytes) -> dict:
    if not data.startswith(b"dex\n") or len(data) < 112:
        raise ValueError("Invalid DEX header")
    ss, so, ts, to, _, _, fs, fo, _, _, cs, co = struct.unpack_from("<12I", data, 56)
    if any(count > 1_000_000 for count in (ss, ts, fs, cs)):
        raise ValueError("Invalid DEX table")
    strings = []
    for i in range(ss):
        _, pos = _uleb(data, _u32(data, so + i * 4))
        end = data.index(b"\0", pos)
        strings.append(data[pos:end].decode("utf-8", "replace"))
    types = [strings[_u32(data, to + i * 4)] for i in range(ts)]
    for i in range(cs):
        row = struct.unpack_from("<8I", data, co + i * 32)
        if types[row[0]] != "Lcom/thingclips/sample/BuildConfig;":
            continue
        pos, static = row[6], row[7]
        if not pos or not static:
            continue
        count, pos = _uleb(data, pos)
        for _ in range(3):
            _, pos = _uleb(data, pos)
        fields, index = [], 0
        for _ in range(count):
            delta, pos = _uleb(data, pos)
            index += delta
            _, pos = _uleb(data, pos)
            fields.append(index)
        count, pos = _uleb(data, static)
        result = {}
        for field in fields[:count]:
            value, pos = _dex_value(data, pos, strings)
            _, _, name = struct.unpack_from("<HHI", data, fo + field * 8)
            result[strings[name]] = value
        return result
    return {}


def _pool_strings(data, start):
    header = struct.unpack_from("<H", data, start + 2)[0]
    count, _, flags, offset, _ = struct.unpack_from("<5I", data, start + 8)
    if count > 100_000:
        raise ValueError("Invalid XML string table")
    result = []
    for i in range(count):
        pos = start + offset + _u32(data, start + header + i * 4)
        if flags & 0x100:
            pos += 2 if data[pos] & 128 else 1  # UTF-16 character count
            length = data[pos]
            pos += 1
            if length & 128:
                length = ((length & 127) << 8) | data[pos]
                pos += 1
            result.append(data[pos:pos + length].decode("utf-8"))
        else:
            length = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if length & 0x8000:
                length = ((length & 0x7fff) << 16) | struct.unpack_from("<H", data, pos)[0]
                pos += 2
            result.append(data[pos:pos + length * 2].decode("utf-16-le"))
    return result


def _manifest(data):
    strings, pos = [], 8
    while pos + 8 <= len(data):
        kind, header, size = struct.unpack_from("<HHI", data, pos)
        if size < header or size < 8 or pos + size > len(data):
            raise ValueError("Invalid XML chunk")
        if kind == 1:
            strings = _pool_strings(data, pos)
        elif kind == 0x102 and strings[_u32(data, pos + 20)] == "manifest":
            start, stride, count = struct.unpack_from("<3H", data, pos + 24)
            if stride < 20:
                raise ValueError("Invalid XML attributes")
            result = {}
            for i in range(count):
                at = pos + 16 + start + stride * i
                name, raw = struct.unpack_from("<II", data, at + 4)
                if raw != 0xffffffff:
                    result[strings[name]] = strings[raw]
                elif data[at + 15] == 3:
                    result[strings[name]] = strings[_u32(data, at + 16)]
            return result
        pos += size
    raise ValueError("Missing app manifest")


def _length_bytes(data, pos=0):
    size = _u32(data, pos)
    end = pos + 4 + size
    if end > len(data):
        raise ValueError("Invalid signing block")
    return data[pos + 4:end], end


def _certificate_digest(file):
    file.seek(0, 2)
    length = file.tell()
    file.seek(max(0, length - 65557))
    tail = file.read()
    end = tail.rfind(b"PK\x05\x06")
    if end < 0:
        raise ValueError("Missing ZIP directory")
    central = _u32(tail, end + 16)
    file.seek(central - 24)
    footer = file.read(24)
    if footer[8:] != b"APK Sig Block 42":
        raise ValueError("This app needs an APK v2/v3 certificate")
    size = struct.unpack_from("<Q", footer)[0]
    if not 24 <= size <= 16 * 1024 * 1024 or size + 8 > central:
        raise ValueError("Invalid APK certificate block")
    file.seek(central - size - 8)
    block = file.read(size + 8)
    if struct.unpack_from("<Q", block)[0] != size:
        raise ValueError("Invalid APK certificate size")
    pos = 8
    while pos < len(block) - 24:
        pair_size = struct.unpack_from("<Q", block, pos)[0]
        if pair_size < 4 or pos + 8 + pair_size > len(block) - 24:
            raise ValueError("Invalid APK signature pair")
        kind = _u32(block, pos + 8)
        if kind in (0x7109871a, 0xf05368c0):
            signers, _ = _length_bytes(block[pos + 12:pos + 8 + pair_size])
            signer, _ = _length_bytes(signers)
            signed, _ = _length_bytes(signer)
            _, at = _length_bytes(signed)
            certs, _ = _length_bytes(signed, at)
            cert, _ = _length_bytes(certs)
            from cryptography import x509
            x509.load_der_x509_certificate(cert)
            return ":".join(f"{b:02X}" for b in hashlib.sha256(cert).digest())
        pos += pair_size + 8
    raise ValueError("Missing app signing certificate")


def _embedded_key(raw: bytes, app_key: str) -> str:
    if not raw.startswith(b"BM"):
        raise ValueError("Invalid SDK key image")
    pixels = raw[_u32(raw, 10):]
    if not pixels:
        raise ValueError("Empty SDK key image")
    value = 0
    for char in app_key:
        value = (31 * value + ord(char)) & 0xffffffff
    if value >= 0x80000000:
        value -= 0x100000000
    start = (abs(value) % len(pixels)) // 2

    def take(pos, size):
        return bytes(pixels[(pos + i) % len(pixels)] for i in range(size))

    count, degree = take(start + 1, 2)
    if not 1 <= count <= 4 or not 1 <= degree <= 16:
        raise ValueError("Unsupported SDK key format")
    pos = start ^ int.from_bytes(take(start + 3, 4), "big")
    points = []
    for _ in range(degree):
        pos %= len(pixels)
        nx = take(pos, 1)[0]
        x = int.from_bytes(take(pos + 1, nx), "big")
        at = pos + 1 + nx
        ny = take(at, 1)[0]
        y = int.from_bytes(take(at + 1, ny), "big")
        points.append((x, y))
        pos ^= int.from_bytes(take(at + 1 + ny, 4), "big")
    if len({x for x, _ in points}) != degree:
        raise ValueError("Invalid SDK key polynomial")
    constant = Fraction(0)
    for x, y in points:
        term = Fraction(y)
        for other, _ in points:
            if other != x:
                term *= Fraction(-other, x - other)
        constant += term
    if constant.denominator != 1 or not 0 < constant < 2 ** 256:
        raise ValueError("Invalid SDK key component")
    key = int(constant).to_bytes(32, "big").decode("ascii")
    if not re.fullmatch(r"[A-Za-z0-9]{32}", key):
        raise ValueError("Invalid SDK key characters")
    return key


def _read(archive, name, limit):
    if archive.getinfo(name).file_size > limit:
        raise ValueError("App package entry exceeds size limit")
    return archive.read(name)


@contextmanager
def _base_apk(path: Path):
    if path.suffix.lower() == ".apk":
        with path.open("rb") as file:
            yield file
        return
    with zipfile.ZipFile(path) as bundle, tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as file:
        names = [n for n in bundle.namelist() if n in ("base.apk", "com.lscsmartconnection.smart.apk", "com.tuya.smart.apk")]
        if len(names) != 1 or bundle.getinfo(names[0]).file_size > _MAX_APK:
            raise ValueError("Missing or oversized base APK")
        with bundle.open(names[0]) as entry:
            while chunk := entry.read(1024 * 1024):
                file.write(chunk)
        file.seek(0)
        yield file


def extract_profile(path: str | Path, brand: str) -> dict:
    if brand not in PACKAGES:
        raise AccountError("Unknown vendor app.", "validation")
    try:
        with _base_apk(Path(path)) as file, zipfile.ZipFile(file) as apk:
            info = _manifest(_read(apk, "AndroidManifest.xml", 8 * 1024 * 1024))
            if info.get("package") != PACKAGES[brand]:
                raise AccountError("This package belongs to a different app. Select the matching LSC Smart Connect or Tuya Smart package.", "validation")
            config = {}
            for name in apk.namelist():
                if re.fullmatch(r"classes\d*\.dex", name):
                    config = _dex_config(_read(apk, name, 64 * 1024 * 1024))
                    if config.get("THING_SMART_APPKEY"):
                        break
            app_key, secret = config.get("THING_SMART_APPKEY", ""), config.get("THING_SMART_SECRET", "")
            if not re.fullmatch(r"[A-Za-z0-9]{20}", app_key) or not re.fullmatch(r"[A-Za-z0-9]{32}", secret):
                raise ValueError("Unsupported SDK configuration")
            embedded = _embedded_key(_read(apk, "assets/t_s.bmp", 1024 * 1024), app_key)
            cert = _certificate_digest(file)
            package = PACKAGES[brand]
            identity = f"{package}_{cert}"
            return {"package": package, "app_key": app_key,
                    "signing_key": f"{identity}_{embedded}_{secret}",
                    "ch_key": hmac.new(app_key.encode(), identity.encode(), hashlib.sha256).hexdigest()[8:16],
                    "app_version": info.get("versionName", "1.0"),
                    "ttid": f"sdk_international@{app_key}" if brand == "lsc" else config.get("THING_SMART_TTID", "android")}
    except AccountError:
        raise
    except (OSError, ValueError, KeyError, IndexError, struct.error, zipfile.BadZipFile, OverflowError, RecursionError):
        raise AccountError("Could not read this app's login configuration. Select a supported, complete APK, APKM or XAPK package.", "app_profile") from None


def profile_for(brand: str, path: str = "") -> dict:
    if brand not in PACKAGES:
        raise AccountError("Unknown vendor app.", "validation")
    if path:
        profile = extract_profile(path, brand)
        profile["source"] = "package"
        return profile
    try:
        saved = vault.get("vendor-app/" + brand)
    except AccountError:
        saved = {}
    # Remember explicit overrides, but let application updates replace the
    # old automatic cache with the current built-in client configuration.
    if (isinstance(saved, dict) and saved.get("source") == "package"
            and saved.get("package") == PACKAGES[brand]
            and all(isinstance(saved.get(key), str) and saved[key]
                    for key in ("app_key", "signing_key", "ch_key", "ttid", "app_version"))):
        return saved
    return builtin_profile(brand)
