"""Real Midea MAS provider.

Verified against the live production gateway during this investigation:

* base  : https://mp-prod.smartmidea.net/mas/v5/app/proxy?alias=<alias>
* sign  : md5(secret + body + sorted_params + random) sent as headers sign/random/secret
* secret: served by native code (libKwProtectSDK.so); supply it via MIDEA_SECRET
* sms   : POST /mj/user/getSmsCode    body {"data": MucData, "iotData": {"mobile", "type": 2}}
* verify: POST /mj/user/mobileVerify  body {"data": MucData, "iotData": {"mobile", "smsCode", "type"}}

Only use this against an account you own.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from datetime import timedelta

from ..domain import new_id, new_token, utc_now
from .base import LoginProvider, UpstreamSession

PROD_BASE = "https://mp-prod.smartmidea.net/mas/v5/app/proxy"
SMS_ALIAS = "/mj/user/getSmsCode"
LOGIN_ALIAS = "/mj/user/mobile/login"
VERIFY_ALIAS = "/mj/user/mobileVerify"  # separate flow; consumes the code, does not issue tokens
PASSWORD_LOGIN_ALIAS = "/v6/mj/user/login"
LOGIN_ID_ALIAS = "/v1/user/login/id/get"  # bare-body call, live-verified
RESET_PASSWORD_ALIAS = "/mj/user/resetPassword"  # 短信验证后可设置新密码

# getSmsCode type: 1 = register, 0 = unsupported, 2 = login, 9 = reset password
SMS_TYPE_LOGIN = 2
SMS_TYPE_RESET = 9

APP_VERSION = "11.5.11.4"

# IOT_KEY candidates recovered from libKwProtectSDK.so by running the APK's own
# decoder (IOTPWManager.decode). Only used when the caller does not inject one.
IOT_KEY_PROD = "PROD_VnoClJI9aikS8dyy"
IOT_KEY_SIT = "SIT_4VjZdg19laDoIrut"
IOT_KEY_DEV = "DEV_TZJn6TXxmtTHclKk"


@dataclass
class GatewayReply:
    http_status: int
    code: int
    msg: str
    data: object


class GatewayError(RuntimeError):
    """Transport-level failure reaching the upstream gateway (maps to HTTP 502)."""


class MideaProvider(LoginProvider):
    def __init__(
        self,
        secret: str,
        base_url: str = PROD_BASE,
        device_id: str = "dsh-login",
        iot_key: str = "",
    ) -> None:
        if not secret:
            raise ValueError("Midea MAS secret is required (supply MIDEA_SECRET)")
        self.secret = secret
        self.base_url = base_url
        self.device_id = device_id
        # IOT_KEY derived by the app from native getIotSecret + IOTPWManager.decode.
        # Supply MIDEA_IOT_KEY once it is recovered; password login needs it.
        self.iot_key = iot_key

    # ------------------------------------------------------------------ transport
    def _capture(self, alias: str, body: dict, reply: GatewayReply) -> None:
        """MIDEA_CAPTURE=1 时把请求/回包落盘，便于事后分析。

        报文里只有哈希后的 password/iampwd，**不含明文密码**。
        """
        if os.environ.get("MIDEA_CAPTURE") != "1":
            return
        try:
            out = Path("probes") / "captures"
            out.mkdir(parents=True, exist_ok=True)
            name = f"{utc_now().strftime('%Y%m%dT%H%M%S')}-{alias.strip('/').replace('/', '_')}.json"
            (out / name).write_text(
                json.dumps(
                    {
                        "alias": alias,
                        "request": body,
                        "response": {
                            "http": reply.http_status,
                            "code": reply.code,
                            "msg": reply.msg,
                            "data": reply.data,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - 采集失败不影响主流程
            return

    def _post(self, alias: str, body: dict, access_token: str = "") -> GatewayReply:
        from ..mas import sign_headers

        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "identity",
            "User-Agent": "okhttp/3.12.0",
            "version": APP_VERSION,
            "platform": "0",
        }
        if access_token:
            # 真机接口用 access-token 头（网关响应头里暴露的就是这个名字）。
            headers["access-token"] = access_token
        headers.update(sign_headers(self.secret, raw))
        request = urllib.request.Request(
            f"{self.base_url}?alias={alias}", data=raw.encode("utf-8"), headers=headers, method="POST"
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        try:
            with opener.open(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
                reply = GatewayReply(
                    response.status,
                    int(payload.get("code", -1)),
                    str(payload.get("msg", "")),
                    payload.get("data"),
                )
                self._capture(alias, body, reply)
                return reply
        except urllib.error.HTTPError as exc:
            reply = GatewayReply(exc.code, -1, exc.read().decode("utf-8", "replace")[:500], None)
            self._capture(alias, body, reply)
            return reply
        except Exception as exc:  # noqa: BLE001 - transport failure (TLS, DNS, timeout)
            raise GatewayError(f"上游网络错误: {type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------------ envelope
    def _muc(self, mobile: str) -> dict:
        return {
            "appKey": "",
            "appVersion": APP_VERSION,
            "deviceId": self.device_id,
            "deviceName": "DSH Login Service",
            "mobile": mobile,
            "osVersion": "13",
            "platform": 0,
        }

    def _envelope(self, mobile: str, iot: dict, include_mobile: bool = True) -> dict:
        stamp = utc_now().strftime("%Y%m%d%H%M%S")
        iot_data: dict = {"reqId": new_id("req"), "stamp": stamp}
        if include_mobile:
            iot_data["mobile"] = mobile
        iot_data.update(iot)
        return {"data": self._muc(mobile), "iotData": iot_data, "timestamp": stamp}

    def _muc_login(self) -> dict:
        """MucData exactly as `getLoginMucData()` builds it: platform = 2, no mobile."""
        return {
            "appKey": "",
            "appVersion": APP_VERSION,
            "deviceId": self.device_id,
            "deviceName": "DSH Login Service",
            "osVersion": "13",
            "platform": 2,
        }

    # ------------------------------------------------------------------ provider API
    def request_sms(self, mobile: str, sms_type: int = SMS_TYPE_LOGIN) -> dict:
        reply = self._post(SMS_ALIAS, self._envelope(mobile, {"type": sms_type}))
        if reply.code != 0:
            raise ValueError(f"getSmsCode failed: {reply.code} {reply.msg}")
        return {"code": reply.code, "msg": reply.msg}

    def verify_sms(
        self, mobile: str, sms_code: str, device_id: str | None, sms_type: int = SMS_TYPE_LOGIN
    ) -> UpstreamSession:
        """Verified step: submit the code to /mj/user/mobileVerify.

        Live behaviour confirmed:
          * success  -> {"code":0, "data":{"randomCode":"<32 hex>"}}
          * the code is single-use and IS consumed by this call

        `randomCode` is *not* a login ticket: the APK only feeds it to
        register / resetPassword. See docs/protocol-notes.md.
        """
        reply = self._post(
            VERIFY_ALIAS,
            self._envelope(mobile, {"smsCode": sms_code, "type": sms_type}),
        )
        if reply.code != 0:
            raise ValueError(f"mobileVerify failed: {reply.code} {reply.msg}")
        random_code = None
        if isinstance(reply.data, dict):
            random_code = reply.data.get("randomCode")
        session = self._session_from(reply.data, mobile)
        session.random_code = random_code  # type: ignore[attr-defined]
        return session

    def _session_from(self, data: object, mobile: str) -> UpstreamSession:
        """Build a session from whatever token material the gateway returned.

        `mobileVerify` returns only `randomCode`, so the access token here is a
        placeholder. Placeholder values are prefixed `unverified_` on purpose so a
        caller can never mistake them for a real Midea credential.
        """
        token = None
        if isinstance(data, dict):
            token = data.get("accessToken") or data.get("jwtToken") or data.get("token")
            if token is None and isinstance(data.get("mdata"), dict):
                token = data["mdata"].get("accessToken")
        now = utc_now()
        if token:
            access = str(token)
        else:
            access = "unverified_" + new_token("midea_access")
        return UpstreamSession(
            user_id=new_id("midea_user"),
            mobile=mobile,
            access_token=access,
            refresh_token="unverified_" + new_token("midea_refresh"),
            expires_at=now + timedelta(seconds=3600),
            refresh_expires_at=now + timedelta(seconds=7 * 24 * 3600),
        )

    # ------------------------------------------------------------------ raw-body calls
    def _post_raw(self, alias: str, body: dict) -> GatewayReply:
        """Some endpoints take a bare JSON body (no CommonReqData envelope).

        Verified from decompiled `LoginApiService.getLoginId`, which sends exactly
        `{"loginAccount": "<account>"}`; the MAS request wrapper then injects `reqId`
        and `stamp` at the JSON root. Without them the gateway answers
        `1002 reqId must not be empty` (live-verified).
        """
        stamped = dict(body)
        stamped.setdefault("reqId", new_id("req"))
        stamped.setdefault("stamp", utc_now().strftime("%Y%m%d%H%M%S"))
        return self._post(alias, stamped)

    def get_login_id(self, account: str) -> str:
        """POST /v1/user/login/id/get -> loginId. Live-verified, needs no password."""
        reply = self._post_raw(LOGIN_ID_ALIAS, {"loginAccount": account})
        if reply.code != 0:
            raise ValueError(f"getLoginId failed: {reply.code} {reply.msg}")
        data = reply.data if isinstance(reply.data, dict) else {}
        login_id = data.get("loginId")
        if not login_id:
            raise ValueError("getLoginId returned no loginId")
        return str(login_id)

    def password_login(self, mobile: str, password: str, login_id: str | None = None) -> UpstreamSession:
        """Real password login: /v6/mj/user/login (alias confirmed from DEX annotations).

        Full flow, recovered from decompiled `LoginApiService`:

          1. ``getLoginId(account)`` -> bare-body POST /v1/user/login/id/get
             (live-verified, returns loginId without a password)
          2. ``passwordLogin(account, password, loginId, dxToken)`` builds
             CommonReqData with IotData:
                 password = SHA256(loginId + SHA256(pwd) + IOT_KEY)   # getEncryptedPwd
                 iampwd   = MD5(MD5(pwd))                             # getIamPwd
                 iotAppId = "900", clientType = 1
             and posts to /v6/mj/user/login.

        IOT_KEY is ``IEncryption.decode(native getIotSecret())``; the native constants
        were recovered by running the APK's own decoder over libKwProtectSDK.so
        (PROD_VnoClJI9aikS8dyy — see docs/protocol-notes.md §8.1).
        """
        from ..crypto_util import encrypted_password, iam_password

        if not self.iot_key:
            raise NotImplementedError(
                "真实密码登录缺少 IOT_KEY（native getIotSecret 经 IOTPWManager.decode 之后的值）。"
                "用 MIDEA_IOT_KEY 注入；生产值已从 libKwProtectSDK.so 还原：PROD_VnoClJI9aikS8dyy"
            )

        resolved_login_id = login_id or self.get_login_id(mobile)

        # 原生 encodeMD5 未做 toLowerCase，按大写处理；MIDEA_IAMPWD_LOWER=1 可回退。
        lower_case = os.environ.get("MIDEA_IAMPWD_LOWER") == "1"
        payload = self._envelope(
            mobile,
            {
                "loginAccount": mobile,
                "loginId": resolved_login_id,
                "password": encrypted_password(resolved_login_id, password, self.iot_key),
                "iampwd": iam_password(password, upper=not lower_case),
                "iotAppId": "900",
                "clientType": 1,
                "appVersion": APP_VERSION,
                "deviceId": self.device_id,
            },
            include_mobile=False,
        )
        payload["data"] = self._muc_login()
        reply = self._post(PASSWORD_LOGIN_ALIAS, payload)
        if reply.code != 0:
            raise ValueError(f"passwordLogin failed: {reply.code} {reply.msg}")
        return self._session_from(reply.data, mobile)

    def reset_password(self, mobile: str, password: str, random_code: str) -> None:
        """POST /mj/user/resetPassword —— 短信授权后设置新密码。

        字段按反编译 `LoginApiService.resetPassword(mobile, password, randomCode)`：
        `password = SHA256(pwd)`（**不含 loginId / IOT_KEY**）、`iampwd = MD5(MD5(pwd))`、
        `randomCode`。因为这两项完全由本地计算，重置后服务端存的就是我们算的值。
        """
        from ..crypto_util import iam_password, sha256_hex

        body = self._envelope(
            mobile,
            {
                "mobile": mobile,
                "password": sha256_hex(password),
                "iampwd": iam_password(password),
                "randomCode": random_code,
            },
        )
        reply = self._post(RESET_PASSWORD_ALIAS, body)
        if reply.code != 0:
            raise ValueError(f"resetPassword failed: {reply.code} {reply.msg}")

    def reset_and_login(self, mobile: str, password: str, sms_code: str) -> UpstreamSession:
        """短信验证 → 重置密码 → 立即密码登录（一次走完，拿真 token）。"""
        verified = self.verify_sms(mobile, sms_code, None, sms_type=SMS_TYPE_RESET)
        random_code = getattr(verified, "random_code", None)
        if not random_code:
            raise ValueError("mobileVerify 未返回 randomCode")
        self.reset_password(mobile, password, str(random_code))
        return self.password_login(mobile, password)

    def refresh(self, refresh_token: str) -> UpstreamSession:
        raise ValueError("upstream refresh route is not implemented in this demo")

    def logout(self, access_token: str) -> None:
        return None
