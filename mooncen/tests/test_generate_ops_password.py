import hashlib

import pytest

from tools import generate_ops_password


def test_encode_password_is_deterministic_with_reviewed_salt() -> None:
    password = "a-strong-ops-password"
    salt = "reviewed-random-salt"

    encoded = generate_ops_password.encode_password(password, salt=salt)
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        generate_ops_password.ROUNDS,
    ).hex()

    assert encoded == f"pbkdf2_sha256$600000${salt}${expected}"
    assert password not in encoded


def test_encode_password_rejects_short_password() -> None:
    with pytest.raises(ValueError, match="between 8 and 256"):
        generate_ops_password.encode_password("short")


def test_encode_password_accepts_exactly_eight_characters() -> None:
    encoded = generate_ops_password.encode_password(
        "12345678",
        salt="reviewed-random-salt",
    )

    assert encoded.startswith("pbkdf2_sha256$600000$reviewed-random-salt$")


def test_read_password_requires_matching_confirmation(monkeypatch) -> None:
    answers = iter(["a-strong-ops-password", "different-password"])
    monkeypatch.setattr(generate_ops_password.getpass, "getpass", lambda _prompt: next(answers))

    with pytest.raises(ValueError, match="do not match"):
        generate_ops_password.read_password()


def test_main_input_mode_prints_only_hash(monkeypatch, capsys) -> None:
    password = "a-strong-ops-password"
    monkeypatch.setattr(generate_ops_password, "read_password", lambda: password)
    monkeypatch.setattr(
        generate_ops_password.secrets,
        "token_urlsafe",
        lambda _length: "reviewed-random-salt",
    )

    assert generate_ops_password.main([]) == 0
    output = capsys.readouterr().out
    assert "MOONCEN_OPS_LOGIN_ID=opsadmin" in output
    assert "MOONCEN_OPS_PASSWORD_HASH=pbkdf2_sha256$600000$" in output
    assert password not in output
    assert "OPS_INITIAL_PASSWORD=" not in output
