import pytest

from jarvis_brain.auth import AuthRateLimiter, DeviceRegistry


def test_add_verify_revoke(tmp_path):
    reg = DeviceRegistry(tmp_path / "devices.json")
    token = reg.add("voiture")
    assert token.startswith("jv_")
    assert reg.verify(token) == "voiture"
    assert reg.verify(token + "x") is None
    assert reg.verify("") is None
    # Le token n'est jamais stocké en clair.
    assert token not in (tmp_path / "devices.json").read_text()
    assert reg.revoke("voiture")
    assert reg.verify(token) is None


def test_rejects_bad_names_and_duplicates(tmp_path):
    reg = DeviceRegistry(tmp_path / "devices.json")
    with pytest.raises(ValueError):
        reg.add("Mon PC!")
    reg.add("pc")
    with pytest.raises(ValueError):
        reg.add("pc")


def test_rate_limiter_blocks_after_failures():
    lim = AuthRateLimiter(max_failures=3)
    for _ in range(3):
        assert not lim.blocked("1.2.3.4")
        lim.fail("1.2.3.4")
    assert lim.blocked("1.2.3.4")
    assert not lim.blocked("5.6.7.8")
