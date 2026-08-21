import base64
import ctypes
import json
from ctypes import wintypes
from pathlib import Path


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_secret(value):
    raw = str(value or "").encode("utf-8")
    incoming, incoming_buffer = _blob(raw)
    outgoing = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(outgoing)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(outgoing.pbData, outgoing.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


def unprotect_secret(value):
    raw = base64.b64decode(value)
    incoming, incoming_buffer = _blob(raw)
    outgoing = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(outgoing)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


class ConfigStore:
    SECRET_FIELDS = ("openai_key", "sapo_api_key", "sapo_api_secret", "sapo_access_token")

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}
        for field in self.SECRET_FIELDS:
            if payload.get(field):
                try:
                    payload[field] = unprotect_secret(payload[field])
                except Exception:
                    payload[field] = ""
        return payload

    def save(self, config):
        payload = dict(config)
        for field in self.SECRET_FIELDS:
            if field in payload:
                payload[field] = protect_secret(payload.get(field, ""))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
