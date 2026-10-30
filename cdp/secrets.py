"""Encrypted credential store for connection strings, passwords and API tokens entered in the admin UI.

* Values are encrypted with Fernet (AES-128-CBC + HMAC) using the master key in CDP_SECRET_KEY.
  Generate one with:  python -m cdp secret-key
* The store is a single file (CDP_SECRETS_PATH, default data/secrets.enc) on the data volume.
* Values are never returned to the UI; only names are listed. Config files refer to a credential by name
  ("connection_env": "ACME_ERP", or "${ACME_TOKEN}" in headers/URLs).
* Lookup order for a name: this store, then the process environment, so .env-based setups keep working.

Without CDP_SECRET_KEY (local development only) a key is generated in data/.secret_key with a warning.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from cdp.config import DATA_DIR, ROOT

log = logging.getLogger("cdp.secrets")
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _path() -> Path:
    value = os.environ.get("CDP_SECRETS_PATH")
    if not value:
        return DATA_DIR / "secrets.enc"
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError("The 'cryptography' package is required for stored credentials "
                           "(pip install cryptography)") from exc
    key = os.environ.get("CDP_SECRET_KEY")
    if not key:
        key_file = DATA_DIR / ".secret_key"
        if key_file.exists():
            key = key_file.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(key)
            log.warning("CDP_SECRET_KEY is not set; generated a development key in %s. "
                        "Set CDP_SECRET_KEY in production.", key_file)
    return Fernet(key.encode() if isinstance(key, str) else key)


def using_dev_key() -> bool:
    return not os.environ.get("CDP_SECRET_KEY")


def _load() -> dict[str, str]:
    path = _path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(store: dict[str, str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def list_names() -> list[str]:
    return sorted(_load())


def set_secret(name: str, value: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError("Use UPPER_CASE letters, digits and underscores (e.g. ACME_ERP_DB), starting with a letter.")
    if not value:
        raise ValueError("The value is empty.")
    store = _load()
    store[name] = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    _save(store)
    log.info("Credential %s saved", name)


def delete_secret(name: str) -> None:
    store = _load()
    if store.pop(name, None) is not None:
        _save(store)
        log.info("Credential %s deleted", name)


def get_secret(name: str) -> str | None:
    """Stored credential first, then the environment variable of the same name."""
    token = _load().get(name)
    if token is not None:
        from cryptography.fernet import InvalidToken
        try:
            return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(f"Credential {name} cannot be decrypted - was CDP_SECRET_KEY changed?") from exc
    return os.environ.get(name)


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()
