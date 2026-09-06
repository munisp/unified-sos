"""Fail-closed behaviour for the PKCS#11 secure-element signer backend."""
import os

import pytest

from edge_daemon.crypto import (
    AdapterUnavailableError,
    DeviceSigner,
    Signer,
    SoftwareSigner,
    get_signer,
)


def _env(**overrides):
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(overrides)
    return env


def test_default_signer_is_software():
    signer = get_signer("POS-TEST-001", env=_env())
    assert isinstance(signer, SoftwareSigner)
    assert isinstance(signer, Signer)
    assert DeviceSigner is SoftwareSigner  # backwards-compatible alias


def test_unknown_backend_fails_closed():
    with pytest.raises(AdapterUnavailableError):
        get_signer("POS-TEST-001", env=_env(EDGE_SIGNER="yubikey-magic"))


def test_se_backend_without_module_path_fails_closed():
    with pytest.raises(AdapterUnavailableError, match="EDGE_SE_PKCS11_MODULE|python-pkcs11"):
        get_signer(
            "POS-TEST-001",
            env=_env(EDGE_SIGNER="se"),  # no EDGE_SE_PKCS11_MODULE
        )


def test_se_backend_without_pkcs11_driver_fails_closed():
    pytest.importorskip("edge_daemon.se_signer")
    from edge_daemon import se_signer

    if se_signer._PKCS11_AVAILABLE:
        pytest.skip("python-pkcs11 installed; missing-driver path not reachable")
    with pytest.raises(AdapterUnavailableError, match="python-pkcs11"):
        se_signer.SecureElementSigner.from_env(
            "POS-TEST-001",
            env=_env(EDGE_SIGNER="se", EDGE_SE_PKCS11_MODULE="/usr/lib/libse.so"),
        )


def test_software_signer_still_verifies_end_to_end():
    from edge_daemon.crypto import verify

    signer = get_signer("POS-TEST-001", env=_env(EDGE_SIGNER="software"))
    msg = b'{"a":1}'
    assert verify(signer.public_key_b64(), msg, signer.sign(msg))
