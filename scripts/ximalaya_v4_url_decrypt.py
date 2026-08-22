#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone Ximalaya Android V4 play/download URL decryptor.

This only decrypts a URL already returned to an authorized App session.  It
does not log in, generate entitlements, or decrypt offline .xm/.x2m/.x3m files.

Usage:
    pip install pycryptodome
    python ximalaya_v4_url_decrypt.py '<ciphertext>' --version 2
    python ximalaya_v4_url_decrypt.py '<ciphertext>' --version auto
"""

from __future__ import annotations

import argparse
import base64
from urllib.parse import urlparse

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


# Android mobile `play_url_key`, used by version 0/1.
MOBILE_PLAY_URL_AES_KEY = bytes.fromhex(
    "5776f21b9e9911388aacfe448068f16a"
)

# Android libencrypt.so version-2 constants.
MOBILE_DOWNLOAD_V2_XOR_KEY = bytes.fromhex(
    "802246a09acfc6ac4f546b03257e04735a046e0a51540adcc4f1678d95b95f31"
)
MOBILE_DOWNLOAD_V2_SUBSTITUTION = bytes.fromhex(
    "2eb9c9b8b136d3bc3fde7c4ea5b3dcc12c4f7b85bba91b1e549757ad1c4aa70f"
    "88b73ce8a3385e89288fac761d064098326d046ed9525b25eb8d9eae87932105"
    "da3d7ed6724d0366f6f7a0ab3ea8efccbfaf81496333b0ed83ec4362a1fa2a9c"
    "f54126753714cde16c64695f9948e7650e95b44723d5e3085642349f15177819"
    "7f9a1f5ac63b29b6a261d8f2ea44cff1f90bee0c2f531a6baac86fe4167782e0"
    "866a119bdd7a597110ca740024fe84fcd1df399df33a27f413fbc7075dbec47d"
    "c39073352b5179ff0d9692708e91678b5c4601d7e64b80dbcb0930bd60d2f00a"
    "a60255ba20e5e250c22db5cec0f84c4531b2d09412d42268a4c58afd18e98c58"
)


def _urlsafe_b64decode(value: str) -> bytes:
    text = str(value or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def decrypt_v2(ciphertext: str) -> str:
    """Decrypt downloadEncryptVersion/version == 2."""
    decoded = _urlsafe_b64decode(ciphertext)
    if len(decoded) < 16:
        raise ValueError("V2 payload is shorter than its 16-byte dynamic key")
    payload, dynamic_key = decoded[:-16], decoded[-16:]
    plain = bytes(
        MOBILE_DOWNLOAD_V2_SUBSTITUTION[value]
        ^ MOBILE_DOWNLOAD_V2_XOR_KEY[index % len(MOBILE_DOWNLOAD_V2_XOR_KEY)]
        ^ dynamic_key[index % len(dynamic_key)]
        for index, value in enumerate(payload)
    )
    return plain.decode("utf-8").strip()


def decrypt_v0_v1(ciphertext: str) -> str:
    """Decrypt version 0/1 with AES-128-ECB and PKCS7 padding."""
    decoded = _urlsafe_b64decode(ciphertext)
    plain = unpad(
        AES.new(MOBILE_PLAY_URL_AES_KEY, AES.MODE_ECB).decrypt(decoded),
        AES.block_size,
    )
    return plain.decode("utf-8").strip()


def looks_like_ximalaya_cdn_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    lower = str(value or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (host.endswith("xmcdn.com") or host.endswith("ximalaya.com"))
        and any(marker in lower for marker in (
            ".mp3", ".m4a", ".aac", ".flac", ".wav", ".mp4",
            "/storages/", "aod.cos",
        ))
    )


def decrypt_mobile_url(ciphertext: str, version: int | None = None) -> str:
    """Decrypt one V4 URL, using the response version when available.

    `version` should come from an entry's `version`, `encryptVersion`, or
    `downloadEncryptVersion`.  When it is missing, V2 is tried first because
    that is the current primary format; only valid Ximalaya CDN URLs are
    accepted during automatic detection.
    """
    text = str(ciphertext or "").strip()
    if text.startswith(("http://", "https://")):
        return text

    versions = [version] if version is not None else [2, 1, 0]
    errors = []
    for candidate_version in versions:
        try:
            plain = (
                decrypt_v2(text)
                if int(candidate_version) == 2
                else decrypt_v0_v1(text)
            )
            if looks_like_ximalaya_cdn_url(plain):
                return plain
            errors.append(f"v{candidate_version}: decrypted value is not a CDN URL")
        except Exception as exc:
            errors.append(f"v{candidate_version}: {exc}")
    raise ValueError("unable to decrypt V4 URL; " + "; ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description="Decrypt a Ximalaya Android V4 URL")
    parser.add_argument("ciphertext", help="playUrl/downloadUrl ciphertext")
    parser.add_argument(
        "--version",
        choices=("auto", "0", "1", "2"),
        default="auto",
        help="value of version/downloadEncryptVersion (default: auto)",
    )
    args = parser.parse_args()
    version = None if args.version == "auto" else int(args.version)
    print(decrypt_mobile_url(args.ciphertext, version))


if __name__ == "__main__":
    main()
