import secrets
import time
import uuid

_TIMESTAMP_MASK = (1 << 48) - 1
_RAND_B_BITS = 62


def uuid7() -> uuid.UUID:
    """UUID version 7 (RFC 9562) : préfixe temporel en millisecondes, donc triable.

    Les index B-tree restent compacts (insertions en fin d'index), contrairement à uuid4.
    Python 3.14 fournira uuid.uuid7 ; cette implémentation couvre 3.12 et 3.13.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    random_bits = secrets.randbits(74)
    value = (timestamp_ms & _TIMESTAMP_MASK) << 80
    value |= 0x7 << 76  # version
    value |= (random_bits >> _RAND_B_BITS) << 64  # rand_a : 12 bits
    value |= 0b10 << 62  # variant RFC 9562
    value |= random_bits & ((1 << _RAND_B_BITS) - 1)  # rand_b : 62 bits
    return uuid.UUID(int=value)
