"""Password hashing with argon2id (argon2-cffi defaults: RFC 9106 low-memory profile)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 256  # bounds hashing cost per request

_hasher = PasswordHasher()
# Verified against when the email is unknown, so both paths take the same time.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


class WeakPasswordError(ValueError):
    pass


def validate_new_password(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPasswordError(f"Password must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise WeakPasswordError(f"Password must be at most {MAX_LENGTH} characters")
    if password.strip() != password or not password.strip():
        raise WeakPasswordError("Password must not start or end with whitespace")


def hash_password(password: str) -> str:
    validate_new_password(password)
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape check: always runs one argon2 verification."""
    if len(password) > MAX_LENGTH:
        password = password[:MAX_LENGTH]
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
