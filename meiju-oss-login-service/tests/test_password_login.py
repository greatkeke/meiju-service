from __future__ import annotations

import hashlib

from app.crypto_util import encrypted_password, iam_password, md5_hex, sha256_hex
from app.main import app
from app.store import store
from fastapi.testclient import TestClient

client = TestClient(app)


def test_sha256_vector():
    assert sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_md5_vector():
    assert md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72"


def test_iam_password_is_double_md5():
    inner_upper = hashlib.md5(b"abc").hexdigest().upper()
    expected = hashlib.md5(inner_upper.encode("ascii")).hexdigest().upper()
    assert iam_password("abc") == expected


def test_iam_password_lowercase_variant_for_comparison():
    expected = hashlib.md5(hashlib.md5(b"abc").hexdigest().encode("ascii")).hexdigest()
    assert iam_password("abc", upper=False) == expected
    assert iam_password("abc") != iam_password("abc", upper=False)


def test_encrypted_password_chain_is_sha256_of_loginid_sha256_pwd_iotkey():
    password = "s3cret"
    iot_key = "K3Y"
    login_id = "lid-1"
    expected = hashlib.sha256(
        f"{login_id}{hashlib.sha256(password.encode()).hexdigest()}{iot_key}".encode()
    ).hexdigest()
    assert encrypted_password(login_id, password, iot_key) == expected
    # changing any input changes the result
    assert encrypted_password(login_id, password, "other") != expected


def test_password_login_success():
    store._sms.clear()
    store._sessions.clear()
    store._by_refresh.clear()

    response = client.post(
        "/v1/login/password",
        json={"mobile": "13800138000", "password": "123456"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"].startswith("local_access_")
    assert body["mobile"] == "13800138000"

    me = client.get("/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["mobile"] == "13800138000"


def test_password_login_wrong_password():
    response = client.post(
        "/v1/login/password",
        json={"mobile": "13800138000", "password": "wrong-password"},
    )
    assert response.status_code == 400
    assert "密码错误" in response.json()["detail"]


def test_password_login_requires_body():
    assert client.post("/v1/login/password", json={"mobile": "13800138000"}).status_code == 422
