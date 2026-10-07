import os
import json
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from app.core.config import get_settings

settings = get_settings()


def get_master_key() -> bytes:
    key_str = settings.KRA_ETIMS_AES_ENCRYPTION_KEY
    key_bytes = key_str.encode("utf-8")
    if len(key_bytes) < 32:
        key_bytes = key_bytes.ljust(32, b"0")
    return key_bytes[:32]


def encrypt_cmc_key(raw_key: str) -> str:
    """Encrypt communication key before storing in PostgreSQL."""
    key = get_master_key()
    iv = os.urandom(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(raw_key.encode("utf-8"), AES.block_size)
    ciphertext = cipher.encrypt(padded)
    combined = iv + ciphertext
    return base64.b64encode(combined).decode("utf-8")


def decrypt_cmc_key(encrypted_str: str) -> str:
    """Decrypt stored communication key for KRA API authentication."""
    key = get_master_key()
    combined = base64.b64decode(encrypted_str.encode("utf-8"))
    iv = combined[:16]
    ciphertext = combined[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = cipher.decrypt(ciphertext)
    return unpad(padded, AES.block_size).decode("utf-8")
