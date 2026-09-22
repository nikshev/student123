#!/usr/bin/env python3
"""Convert an RSA private key (PEM, PKCS#1 from ssh-keygen -m PEM) to JWK JSON.

Stdlib only: minimal DER reader for PKCS#1 RSAPrivateKey
(SEQUENCE of INTEGERs: version, n, e, d, p, q, dp, dq, qinv).
Used by docker/init/init-secrets.sh to produce JWT JWK sets for LMS/CMS SSO.

Usage:
    python3 pem_to_jwk.py <private.pem>
Prints two lines:
    PRIVATE_JSON=<...>   # {"kid":"openedx","kty":"RSA","e":..,"d":..,"n":..,"p":..,"q":..,"dq":..,"dp":..,"qi":..}
    PUBLIC_JSON=<...>    # {"keys":[{"kid":"openedx","kty":"RSA","e":..,"n":..}]}
"""

import base64
import json
import sys


def _b64url(num: int) -> str:
    raw = num.to_bytes((num.bit_length() + 7) // 8 or 1, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _read_der(data: bytes, pos: int):
    """Read one DER TLV. Returns (tag, value_bytes, next_pos)."""
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        nbytes = length & 0x7F
        if nbytes == 0 or nbytes > 4:
            raise ValueError("unsupported DER length")
        length = int.from_bytes(data[pos:pos + nbytes], "big")
        pos += nbytes
    return tag, data[pos:pos + length], pos + length


def _parse_pkcs1(private_der: bytes) -> dict:
    tag, seq, end = _read_der(private_der, 0)
    if tag != 0x30 or end != len(private_der):
        raise ValueError("expected PKCS#1 SEQUENCE")
    names = ["version", "n", "e", "d", "p", "q", "dp", "dq", "qinv"]
    out = {}
    pos = 0
    for name in names:
        tag, value, pos = _read_der(seq, pos)
        if tag != 0x02:
            raise ValueError(f"expected INTEGER for {name}")
        out[name] = int.from_bytes(value, "big")
    if out["version"] != 0:
        raise ValueError("only PKCS#1 v1.5 two-prime keys supported")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <private.pem>", file=sys.stderr)
        return 2
    with open(sys.argv[1], "rb") as handle:
        pem = handle.read().decode("ascii")
    b64 = "".join(
        line.strip() for line in pem.splitlines() if "-----" not in line
    )
    key = _parse_pkcs1(base64.b64decode(b64))
    private = {
        "kid": "openedx",
        "kty": "RSA",
        "e": _b64url(key["e"]),
        "d": _b64url(key["d"]),
        "n": _b64url(key["n"]),
        "p": _b64url(key["p"]),
        "q": _b64url(key["q"]),
        "dq": _b64url(key["dq"]),
        "dp": _b64url(key["dp"]),
        "qi": _b64url(key["qinv"]),
    }
    public = {
        "keys": [
            {
                "kid": "openedx",
                "kty": "RSA",
                "e": _b64url(key["e"]),
                "n": _b64url(key["n"]),
            }
        ]
    }
    # Single quotes around the JSON: init-secrets.sh eval()s this output,
    # and bare double quotes would be stripped by the shell. JSON produced
    # by json.dumps never contains single quotes, so this is safe.
    print("PRIVATE_JSON='" + json.dumps(private, separators=(",", ":")) + "'")
    print("PUBLIC_JSON='" + json.dumps(public, separators=(",", ":")) + "'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
