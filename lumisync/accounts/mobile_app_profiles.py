"""Shared Tuya mobile-client configuration for password sign-in.

Extracted from the distribution apps listed below. These values identify and
sign requests for the vendor app namespace; they are not user credentials.
Every account still requires its password, any vendor verification, and an
authenticated session. The service is checked before a password is submitted.

Keep this configuration separate from saved account sessions. Vendor changes
can require updated configuration; a matching app package remains an optional
override. Do not add account emails, passwords, session tokens or local keys.
"""

from __future__ import annotations

from copy import deepcopy

PROFILE_SOURCES = {'lsc': {'filename': 'LSC+Smart+Connect_2.0.7_APKPure.xapk',
         'sha256': 'c693cc30ee605c08c2fc87ad22b926493609725d94fd20aa43b18b04640946d0'},
 'tuya': {'filename': 'com.tuya.smart_7.11.4-866_2arch_9e039da7aea7ed6dd1b0dfb8ab5f3ef5_apkmirror.com.apkm',
          'sha256': '124e882dece84628035a04455c9344908588fa51f9b92e039914a0721a1a4ae7'}}

_PROFILES = {'lsc': {'package': 'com.lscsmartconnection.smart',
         'app_key': 'ufhnhpkh9np8t3rs9qda',
         'signing_key': 'com.lscsmartconnection.smart_28:23:66:B3:FD:B7:55:81:63:B8:FD:71:02:DA:3F:3E:F4:8D:E9:BC:66:06:93:FB:19:C7:8B:CF:5D:1A:3B:26_qmcy7pp5kafkdgq8cp3n45eqcrpwu5ng_4c93vt3cm87a4wdgwas8hgvnhcdtt8xy',
         'ch_key': '8ab6879a',
         'app_version': '2.0.7',
         'ttid': 'sdk_international@ufhnhpkh9np8t3rs9qda'},
 'tuya': {'package': 'com.tuya.smart',
          'app_key': '3cxxt3au9x33ytvq3h9j',
          'signing_key': 'com.tuya.smart_93:21:9F:C2:73:E2:20:0F:4A:DE:E5:F7:19:1D:C6:56:BA:2A:2D:7B:2F:F5:D2:4C:D5:5C:4B:61:55:00:1E:40_f3hd7pet4p83kemjdf5wqsa5tavrv579_5gdtanjtf38vyxkqh87cjwfcqjhvjjqa',
          'ch_key': '3f7060ea',
          'app_version': '7.11.4',
          'ttid': 'tuyaSmart'}}


def builtin_profile(brand: str) -> dict:
    """Return an independent copy of the matching shared client configuration."""
    return deepcopy(_PROFILES[brand])
