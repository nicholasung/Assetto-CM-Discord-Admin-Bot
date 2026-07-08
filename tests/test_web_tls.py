"""HTTPS wiring for the web UI: preflight, self-signed generation, ssl context."""

from __future__ import annotations

import shutil
import ssl
import subprocess
import sys

import pytest

from acbot.config import Config, WebConfig
from acbot.web.tls import (
    WebTLSError,
    build_ssl_context,
    ensure_self_signed,
    tls_preflight,
)


def _crypto_backend_works() -> bool:
    """True only if cryptography can actually generate a key here. Probed in a
    subprocess: some prebuilt wheels fail to link their native backend (a hard
    Rust panic/abort), which we must not let crash the test process. The wheel on
    the Windows VM / CI works, so these tests run there."""
    code = ("from cryptography.hazmat.primitives.asymmetric import rsa;"
            "rsa.generate_private_key(public_exponent=65537, key_size=2048)")
    try:
        return subprocess.run([sys.executable, "-c", code],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


CRYPTO_OK = _crypto_backend_works()
needs_crypto = pytest.mark.skipif(not CRYPTO_OK, reason="cryptography backend unavailable")


def _cfg(tmp_path, **web) -> Config:
    cfg = Config(base_dir=tmp_path)
    cfg.web = WebConfig(**web)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def openssl_pair(tmp_path):
    """A real cert/key pair made with the openssl CLI (no cryptography needed)."""
    if not shutil.which("openssl"):
        pytest.skip("openssl CLI not available")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return cert, key


# -- config / preflight (no crypto needed) -----------------------------------

def test_tls_off_builds_no_context(tmp_path):
    cfg = _cfg(tmp_path, tls=False)
    assert tls_preflight(cfg) is None
    assert build_ssl_context(cfg) is None


def test_preflight_requires_both_cert_and_key(tmp_path):
    cfg = _cfg(tmp_path, tls=True, tls_cert="only_cert.pem")
    assert "both" in tls_preflight(cfg).lower()


def test_preflight_reports_missing_files(tmp_path):
    cfg = _cfg(tmp_path, tls=True, tls_cert="nope_cert.pem", tls_key="nope_key.pem")
    msg = tls_preflight(cfg)
    assert msg and "not found" in msg


def test_bad_cert_file_raises(tmp_path):
    cert, key = tmp_path / "bad.pem", tmp_path / "bad_key.pem"
    cert.write_text("not a cert")
    key.write_text("not a key")
    cfg = _cfg(tmp_path, tls=True, tls_cert=str(cert), tls_key=str(key))
    with pytest.raises(WebTLSError):
        build_ssl_context(cfg)


# -- provided cert/key (stdlib ssl path; cert from openssl CLI) ---------------

def test_provided_cert_is_loaded(tmp_path, openssl_pair):
    cert, key = openssl_pair
    cfg = _cfg(tmp_path, tls=True, tls_cert=str(cert), tls_key=str(key))
    assert tls_preflight(cfg) is None
    assert isinstance(build_ssl_context(cfg), ssl.SSLContext)


# -- self-signed auto-generation (needs a working cryptography backend) -------

@needs_crypto
def test_self_signed_generation_and_context(tmp_path):
    cfg = _cfg(tmp_path, tls=True)  # no cert/key -> auto self-signed
    assert tls_preflight(cfg) is None
    ctx = build_ssl_context(cfg)
    assert isinstance(ctx, ssl.SSLContext)
    assert cfg.web_cert_path.is_file() and cfg.web_key_path.is_file()


@needs_crypto
def test_self_signed_is_reused_not_regenerated(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    ensure_self_signed(cert, key)
    first = cert.read_bytes()
    ensure_self_signed(cert, key)  # valid + present -> left alone
    assert cert.read_bytes() == first
