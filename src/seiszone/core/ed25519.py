"""Ed25519 signatures (RFC 8032), after the RFC's own reference code (section 6), so updates can
be checked without an extra library in the program. Used to check that an update was signed by
the SeismoVerse publisher (core/updater.py) and, on the build PC, to sign it (installer/publish.py).

Only public data is handled when checking. It is not constant-time, which only matters for
signing (done on the publisher's own PC); checking needs no secret. Tested with the RFC 8032
test vectors and against OpenSSL (tests/test_updater.py).
"""

import hashlib

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493      # order of the base point


def _inv(x):
    return pow(x, P - 2, P)


D = -121665 * _inv(121666) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _sha512_modq(data):
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % Q


# points in extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z
def _add(p1, p2):
    a = (p1[1] - p1[0]) * (p2[1] - p2[0]) % P
    b = (p1[1] + p1[0]) * (p2[1] + p2[0]) % P
    c = 2 * p1[3] * p2[3] * D % P
    d = 2 * p1[2] * p2[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, pt):
    out = (0, 1, 1, 0)                     # the neutral element
    while s > 0:
        if s & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        s >>= 1
    return out


def _equal(p1, p2):
    return ((p1[0] * p2[2] - p2[0] * p1[2]) % P == 0
            and (p1[1] * p2[2] - p2[1] * p1[2]) % P == 0)


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def _compress(pt):
    zinv = _inv(pt[2])
    x, y = pt[0] * zinv % P, pt[1] * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _expand(secret):
    if len(secret) != 32:
        raise ValueError("an Ed25519 secret key has 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret):
    """The 32-byte public key of a 32-byte secret key."""
    a, _prefix = _expand(secret)
    return _compress(_mul(a, G))


def sign(secret, message):
    """The 64-byte signature of message (bytes) with a 32-byte secret key."""
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, G))
    r = _sha512_modq(prefix + message)
    rs = _compress(_mul(r, G))
    h = _sha512_modq(rs + pub + message)
    s = (r + h * a) % Q
    return rs + int.to_bytes(s, 32, "little")


def verify(public, message, signature):
    """True only when signature (64 bytes) is a valid signature of message by public (32 bytes)."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    if a is None:
        return False
    rs = signature[:32]
    r = _decompress(rs)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = _sha512_modq(rs + public + message)
    return _equal(_mul(s, G), _add(r, _mul(h, a)))
