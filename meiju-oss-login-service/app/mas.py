"""MAS gateway request signing.

Reconstructed from `MideaMasSignPolicy.sign` (dexdump of classes8.dex):

    content = (json body) + concat(sorted path "k" + "v", alias excluded)
    sign    = md5(secret + content + random)          -> lowercase hex
    headers = sign, random (= System.currentTimeMillis()), secret

The secret for the production gateway is served by native code
(`EnvConfig$PROConfig.getMucSecret` is a native method in libKwProtectSDK.so).
"""

from __future__ import annotations

import hashlib
import time


def current_random() -> str:
    """`MideaMasSignPolicy.getRandom()` == String.valueOf(System.currentTimeMillis())."""
    return str(int(time.time() * 1000))


def mas_sign(secret: str, body: str = "", path_params: dict[str, str] | None = None,
             random: str | None = None) -> str:
    params = path_params or {}
    content = body + "".join(key + params[key] for key in sorted(params))
    return hashlib.md5((secret + content + (random or current_random())).encode("utf-8")).hexdigest()


def sign_headers(secret: str, body: str = "", path_params: dict[str, str] | None = None,
                 random: str | None = None) -> dict[str, str]:
    nonce = random or current_random()
    return {
        "sign": mas_sign(secret, body, path_params, nonce),
        "random": nonce,
        "secret": secret,
    }
