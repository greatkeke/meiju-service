from __future__ import annotations

from app.mas import mas_sign, sign_headers

# Live-verified vector: the production gateway accepted this exact signature
# (headers sign/random/secret, empty body, no path parameters).
SECRET = "prod_secret123@muc"
RANDOM = "1792236849602"
EXPECTED = "e277c3032f6fd76d39db11de6f76eae7"


def test_signature_matches_live_accepted_vector():
    assert mas_sign(SECRET, "", {}, RANDOM) == EXPECTED


def test_body_participates_in_signature():
    assert mas_sign(SECRET, '{"a":1}', {}, RANDOM) != mas_sign(SECRET, "", {}, RANDOM)


def test_path_params_are_sorted_and_concatenated():
    one = mas_sign(SECRET, "", {"b": "2", "a": "1"}, RANDOM)
    two = mas_sign(SECRET, "", {"a": "1", "b": "2"}, RANDOM)
    assert one == two


def test_sign_headers_shape():
    headers = sign_headers(SECRET, "", {}, RANDOM)
    assert set(headers) == {"sign", "random", "secret"}
    assert headers["random"] == RANDOM
    assert headers["secret"] == SECRET
    assert len(headers["sign"]) == 32
