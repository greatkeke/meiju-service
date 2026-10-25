"""Password-login cryptography, recovered from bytecode.

Source: `LoginApiService.passwordLogin` (classes7.dex) and `LoginParamsUtil`:

    builder = loginId + SecurityUtils.encodeSHA256(password) + HttpUtil.IOT_KEY
    iotData.password = getEncryptedPwd(builder)   # = SecurityUtils.encodeSHA256(builder)
    iotData.iampwd   = getIamPwd(x)               # = encodeMD5(encodeMD5(x))

i.e. the "password" field is a chained SHA256, "iampwd" a double MD5. Both are
implemented here verbatim; nothing is invented.
"""

from __future__ import annotations

import hashlib


def sha256_hex(value: str) -> str:
    """SecurityUtils.encodeSHA256 — lowercase hex digest of UTF-8 bytes."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def md5_hex(value: str) -> str:
    """SecurityUtils.encodeMD5 — lowercase hex digest of UTF-8 bytes."""
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def iam_password(value: str, upper: bool = True) -> str:
    """getIamPwd: MD5 applied twice by the *native* EncodeAndDecodeUtils.

    Native `encodeMD5` is used verbatim (no `.toLowerCase()` at the call site,
    unlike `encodeSHA256` which lowercases explicitly), so its hex output is
    taken to be UPPERCASE — and the second pass hashes that uppercase string.
    `upper=False` reproduces a plain lowercase double-MD5 for comparison.
    """
    inner = md5_hex(value)
    if upper:
        inner = inner.upper()
    outer = md5_hex(inner)
    return outer.upper() if upper else outer


def encrypted_password(login_id: str, password: str, iot_key: str) -> str:
    """getEncryptedPwd(loginId + SHA256(pwd) + IOT_KEY) — SHA256 is lowercased by the app."""
    return sha256_hex(f"{login_id}{sha256_hex(password)}{iot_key}")
