from __future__ import annotations

import os

from pydantic import BaseModel


class Settings(BaseModel):
    provider: str = "local"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    debug_local_codes: bool = True

    # real upstream (only used when provider == "midea")
    midea_secret: str = ""
    midea_base: str = "https://mp-prod.smartmidea.net/mas/v5/app/proxy"
    midea_device_id: str = "dsh-login"
    midea_iot_key: str = ""
    # 真机设备层的 accessToken：拿官方 App 的 token 注入即可列出/控制真实设备
    midea_access_token: str = ""

    # local password login (provider == "local")
    local_password: str = "123456"


def load_settings() -> Settings:
    return Settings(
        provider=os.environ.get("MIDEA_PROVIDER", "local"),
        midea_secret=os.environ.get("MIDEA_SECRET", ""),
        midea_base=os.environ.get("MIDEA_BASE", "https://mp-prod.smartmidea.net/mas/v5/app/proxy"),
        midea_device_id=os.environ.get("MIDEA_DEVICE_ID", "dsh-login"),
        midea_iot_key=os.environ.get("MIDEA_IOT_KEY", ""),
        midea_access_token=os.environ.get("MIDEA_ACCESS_TOKEN", ""),
        local_password=os.environ.get("LOCAL_PASSWORD", "123456"),
    )


settings = load_settings()
