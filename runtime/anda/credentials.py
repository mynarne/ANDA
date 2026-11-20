"""OS credential store for connector tokens. Config keeps only the ANDA_*_TOKEN name.

Windows: Credential Manager (CredWriteW/CredReadW). macOS: login keychain through
`security -i` on stdin, so a secret never appears in a process argument list.
Elsewhere there is no store and the protected environment variable stays the only source.
"""
import ctypes
import os
import re
import subprocess
import sys
from .core import AndaError

ACCOUNT = "ANDA"
NAME = re.compile(r"ANDA_[A-Z0-9_]+_(TOKEN|KEY)")
# Notion (ntn_/secret_) and Discord bot tokens fit this; it also keeps `security -i` free of quoting.
TOKEN = re.compile(r"[A-Za-z0-9._-]{20,200}")


class _Credential(ctypes.Structure):
    # CREDENTIALW with portable field types (FILETIME = two DWORDs).
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_uint32 * 2),
                ("CredentialBlobSize", ctypes.c_uint32), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


GENERIC, PERSIST_LOCAL_MACHINE = 1, 2  # never CRED_PERSIST_ENTERPRISE: no roaming copy


def _advapi():
    return ctypes.WinDLL("advapi32")


def _target(name):
    return ACCOUNT + ":" + name


def label(platform=None):
    return {"win32": "windows_credential_manager", "darwin": "macos_keychain"}.get(platform or sys.platform)


def read(name, platform=None):
    """Stored value or None. Any store failure reads as missing, so connectors stay blocked."""
    platform = platform or sys.platform
    if not NAME.fullmatch(name):
        raise AndaError("Only ANDA_*_TOKEN/KEY references are accepted")
    if platform == "win32":
        found = ctypes.POINTER(_Credential)()
        try:
            api = _advapi()
            if not api.CredReadW(_target(name), GENERIC, 0, ctypes.pointer(found)):
                return None
        except OSError:
            return None
        try:
            item = found.contents
            return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode("utf-16-le")
        except UnicodeError:
            return None
        finally:
            api.CredFree(found)
    if platform == "darwin":
        try:
            run = subprocess.run(["security", "find-generic-password", "-a", ACCOUNT, "-s", name, "-w"],
                                 capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return run.stdout.decode("utf-8").rstrip("\n") if run.returncode == 0 else None
    return None


def write(name, secret, platform=None):
    """Store and read back. Returns the store label, or None where no OS store is supported."""
    platform = platform or sys.platform
    if not NAME.fullmatch(name):
        raise AndaError("Only ANDA_*_TOKEN/KEY references are accepted")
    if not isinstance(secret, str) or not TOKEN.fullmatch(secret):
        raise AndaError("Unexpected token format; nothing was stored")
    if platform == "win32":
        blob = secret.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        item = _Credential(Type=GENERIC, TargetName=_target(name), CredentialBlobSize=len(blob),
                           CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                           Persist=PERSIST_LOCAL_MACHINE, UserName=ACCOUNT)
        if not _advapi().CredWriteW(ctypes.pointer(item), 0):
            raise AndaError("Windows Credential Manager rejected the write")
    elif platform == "darwin":
        command = "add-generic-password -U -a " + ACCOUNT + " -s " + name + " -w " + secret + "\n"
        try:
            subprocess.run(["security", "-i"], input=command.encode("utf-8"), capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise AndaError("macOS Keychain is unavailable") from None
    else:
        return None
    # `security -i` can exit 0 after a failed command; only a matching read-back counts.
    if read(name, platform) != secret:
        raise AndaError("Credential store read-back did not match; use the protected environment instead")
    return label(platform)


def load_env(config, every=False):
    """Fill missing ANDA_*_TOKEN variables of enabled (or, for provisioning, all) connectors for this process only.

    An explicit environment value always wins. Existing env-based protections
    (redaction, child-process scrubbing) then cover stored values too.
    """
    loaded = []
    for spec in config.get("destinations", {}).values():
        name = spec.get("token_env", "")
        if (every or spec.get("enabled")) and NAME.fullmatch(name) and not os.environ.get(name):
            value = read(name)
            if value:
                os.environ[name] = value
                loaded.append(name)
    return loaded
