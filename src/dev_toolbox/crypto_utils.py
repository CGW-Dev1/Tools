from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


HASH_ALGORITHMS: list[tuple[str, str, int]] = [
    ("MD5", "md5", 128),
    ("SHA1", "sha1", 160),
    ("SHA224", "sha224", 224),
    ("SHA256", "sha256", 256),
    ("SHA384", "sha384", 384),
    ("SHA512", "sha512", 512),
    ("SHA3-224", "sha3_224", 224),
    ("SHA3-256", "sha3_256", 256),
    ("SHA3-384", "sha3_384", 384),
    ("SHA3-512", "sha3_512", 512),
    ("BLAKE2s", "blake2s", 256),
    ("BLAKE2b", "blake2b", 512),
]

HMAC_ALGORITHMS: list[tuple[str, str, int]] = [
    ("HMAC-MD5", "md5", 128),
    ("HMAC-SHA1", "sha1", 160),
    ("HMAC-SHA256", "sha256", 256),
    ("HMAC-SHA512", "sha512", 512),
]

ENCODINGS = {
    "UTF-8": "utf-8",
    "GB18030": "gb18030",
    "UTF-16LE": "utf-16-le",
    "UTF-16BE": "utf-16-be",
}


@dataclass
class DigestRow:
    kind: str
    algorithm: str
    bits: int
    hex_digest: str
    base64_digest: str


def encoding_name(label: str) -> str:
    return ENCODINGS.get(label, "utf-8")


def digest_text(
    text: str,
    *,
    encoding_label: str = "UTF-8",
    uppercase: bool = False,
    hmac_secret: str = "",
) -> list[DigestRow]:
    encoding = encoding_name(encoding_label)
    data = text.encode(encoding)
    key = hmac_secret.encode(encoding) if hmac_secret else None
    return digest_bytes(data, uppercase=uppercase, hmac_key=key)


def digest_file(
    path: str | Path,
    *,
    uppercase: bool = False,
    hmac_secret: str = "",
    hmac_encoding_label: str = "UTF-8",
) -> list[DigestRow]:
    hashers = [(display, bits, hashlib.new(name)) for display, name, bits in HASH_ALGORITHMS]
    hmac_key = hmac_secret.encode(encoding_name(hmac_encoding_label)) if hmac_secret else None
    hmacers = [(display, bits, hmac.new(hmac_key, digestmod=name)) for display, name, bits in HMAC_ALGORITHMS] if hmac_key else []
    with Path(path).open("rb") as handle:
        _update_all(handle, [item[2] for item in hashers], [item[2] for item in hmacers])

    rows: list[DigestRow] = []
    for display, bits, hasher in hashers:
        rows.append(_row("Hash", display, bits, hasher.digest(), uppercase))
    for display, bits, hasher in hmacers:
        rows.append(_row("HMAC", display, bits, hasher.digest(), uppercase))
    return rows


def digest_bytes(data: bytes, *, uppercase: bool = False, hmac_key: bytes | None = None) -> list[DigestRow]:
    rows: list[DigestRow] = []
    for display, name, bits in HASH_ALGORITHMS:
        rows.append(_row("Hash", display, bits, hashlib.new(name, data).digest(), uppercase))
    if hmac_key:
        for display, name, bits in HMAC_ALGORITHMS:
            rows.append(_row("HMAC", display, bits, hmac.new(hmac_key, data, name).digest(), uppercase))
    return rows


def rows_to_text(rows: list[DigestRow]) -> str:
    lines = ["类型\t算法\t位数\tHex结果\tBase64结果"]
    lines.extend(f"{row.kind}\t{row.algorithm}\t{row.bits}\t{row.hex_digest}\t{row.base64_digest}" for row in rows)
    return "\n".join(lines)


def _update_all(handle: BinaryIO, hashers: list[object], hmacers: list[object]) -> None:
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        for hasher in hashers:
            hasher.update(chunk)
        for hmacer in hmacers:
            hmacer.update(chunk)


def _row(kind: str, display: str, bits: int, digest: bytes, uppercase: bool) -> DigestRow:
    hex_digest = digest.hex()
    if uppercase:
        hex_digest = hex_digest.upper()
    return DigestRow(
        kind=kind,
        algorithm=display,
        bits=bits,
        hex_digest=hex_digest,
        base64_digest=base64.b64encode(digest).decode("ascii"),
    )
