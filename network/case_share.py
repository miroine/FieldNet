"""Password-protected case packages.

File format (.fncase):  b'FNCASE1' | salt(16) | nonce(12) | AES-256-GCM ciphertext(+tag)
Key = scrypt(password, salt, n=2**15, r=8, p=1). The header is authenticated as associated data, so a wrong password
or any modification raises ``ShareError``. A *share link* is the same bytes URL-safe-base64 encoded; it is a
self-contained payload (the data travels inside the text), not a pointer to a server."""
from __future__ import annotations
import base64, hashlib, os, zlib
from network.case_manager import CaseLibrary, export_json, import_json

MAGIC = b'FNCASE1'
LINK_PREFIX = 'fieldnet-share:'
MIN_PASSWORD = 8
MAX_LINK_CHARS = 1_500_000   # beyond this a pasted token becomes unreliable in browsers / chat tools


class ShareError(ValueError): pass


def _aes():
    try: from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc: raise ShareError('The "cryptography" package is required for password sharing (pip install cryptography)') from exc
    return AESGCM


def _key(password, salt): return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def password_problem(password):
    if not password or len(password) < MIN_PASSWORD: return f'Use at least {MIN_PASSWORD} characters.'
    if password.lower() == password and password.isalpha(): return 'Mix letters with digits or symbols.'
    return None


def encrypt_bytes(data: bytes, password: str) -> bytes:
    p = password_problem(password)
    if p: raise ShareError(p)
    salt, nonce = os.urandom(16), os.urandom(12); header = MAGIC + salt + nonce
    return header + _aes()(_key(password, salt)).encrypt(nonce, zlib.compress(data, 9), MAGIC)


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28 + 16: raise ShareError('Not a FieldNet share package.')
    o = len(MAGIC); salt, nonce, ct = blob[o:o + 16], blob[o + 16:o + 28], blob[o + 28:]
    try: return zlib.decompress(_aes()(_key(password, salt)).decrypt(nonce, ct, MAGIC))
    except ShareError: raise
    except Exception as exc: raise ShareError('Wrong password, or the package was modified.') from exc


def share_package(lib: CaseLibrary, ids, password) -> bytes: return encrypt_bytes(export_json(lib, ids), password)
def open_package(blob: bytes, password) -> CaseLibrary:
    try: return import_json(decrypt_bytes(blob, password))
    except ShareError: raise
    except Exception as exc: raise ShareError(f'Package opened but is not a valid case file: {exc}') from exc


def to_link(blob: bytes) -> str:
    s = LINK_PREFIX + base64.urlsafe_b64encode(blob).decode('ascii')
    return s


def from_link(text: str) -> bytes:
    t = ''.join((text or '').split())
    if t.startswith(LINK_PREFIX): t = t[len(LINK_PREFIX):]
    elif '#' in t and LINK_PREFIX.strip(':') in t: t = t.split(LINK_PREFIX, 1)[-1]
    try: return base64.b64decode(t + '=' * (-len(t) % 4), altchars=b'-_', validate=True)
    except Exception as exc: raise ShareError('The link is not valid (truncated or altered while copying?).') from exc


def link_ok(link): return len(link) <= MAX_LINK_CHARS
