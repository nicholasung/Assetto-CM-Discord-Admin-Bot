"""TLS for the admin web UI.

Two ways to serve HTTPS:
  * point `web.tls_cert` / `web.tls_key` at your own PEM files (a real CA cert,
    Let's Encrypt, an internal CA…) — handled by stdlib ssl, no extra deps; or
  * leave them unset and we generate a long-lived self-signed cert into the data
    dir on first run (browsers show a one-time trust warning, but the password
    and session cookie are still encrypted on the wire). That path needs the
    `cryptography` package.

Only the admin UI needs this — it's what carries the password/session. The
content download server (port 8082) serves public files with no credentials.
"""

from __future__ import annotations

import logging
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import Config

log = logging.getLogger(__name__)

_MISSING_CRYPTO = (
    "web.tls is on with no tls_cert/tls_key, so a self-signed certificate must be "
    "generated — that needs the 'cryptography' package (pip install cryptography). "
    "Alternatively set web.tls_cert and web.tls_key to your own PEM files."
)


class WebTLSError(Exception):
    """A user-facing problem configuring HTTPS for the web UI."""


def tls_preflight(cfg: Config) -> str | None:
    """Cheap up-front check: returns an error string, or None if TLS is OK/off."""
    if not cfg.web.tls:
        return None
    if cfg.web.tls_cert or cfg.web.tls_key:
        if not (cfg.web.tls_cert and cfg.web.tls_key):
            return "web.tls_cert and web.tls_key must both be set (or both left unset)."
        missing = [str(cfg.resolve_path(p))
                   for p in (cfg.web.tls_cert, cfg.web.tls_key)
                   if not cfg.resolve_path(p).is_file()]
        if missing:
            return "web TLS cert/key file(s) not found: " + ", ".join(missing)
        return None
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return _MISSING_CRYPTO
    return None


def build_ssl_context(cfg: Config) -> ssl.SSLContext | None:
    """SSLContext for the web server, or None when TLS is disabled.

    Raises WebTLSError on any misconfiguration (never silently downgrades to
    plaintext — the whole point is that credentials aren't sent in the clear).
    """
    if not cfg.web.tls:
        return None
    cert_path, key_path = _resolve_cert_and_key(cfg)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except (ssl.SSLError, OSError) as e:
        raise WebTLSError(f"could not load TLS cert/key ({cert_path}, {key_path}): {e}") from e
    return ctx


def _resolve_cert_and_key(cfg: Config) -> tuple[Path, Path]:
    if cfg.web.tls_cert and cfg.web.tls_key:
        cert = cfg.resolve_path(cfg.web.tls_cert)
        key = cfg.resolve_path(cfg.web.tls_key)
        if not cert.is_file() or not key.is_file():
            raise WebTLSError(f"TLS cert/key not found: {cert}, {key}")
        return cert, key
    cert, key = cfg.web_cert_path, cfg.web_key_path
    ensure_self_signed(cert, key)
    return cert, key


def ensure_self_signed(cert_path: Path, key_path: Path,
                       extra_hosts: list[str] | None = None) -> None:
    """Generate a self-signed cert/key pair if one isn't already present/valid."""
    if cert_path.is_file() and key_path.is_file() and not _expired(cert_path):
        return
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as e:
        raise WebTLSError(_MISSING_CRYPTO) from e

    hosts = _san_hosts(extra_hosts)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "acbot web UI")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(hosts), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    try:
        key_path.chmod(0o600)  # best-effort; a no-op on Windows
    except OSError:
        pass
    log.info("generated self-signed web TLS cert at %s (valid ~10 years)", cert_path)


def _expired(cert_path: Path) -> bool:
    try:
        from cryptography import x509
    except ImportError:
        return False  # can't check; assume the existing file is usable
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (ValueError, OSError):
        return True  # unreadable/corrupt -> regenerate
    try:
        not_after = cert.not_valid_after_utc
    except AttributeError:  # cryptography < 42
        not_after = cert.not_valid_after.replace(tzinfo=UTC)
    return not_after <= datetime.now(UTC)


def _san_hosts(extra: list[str] | None):
    """SANs so localhost/host name/local IPs at least validate to themselves."""
    import ipaddress
    import socket

    from cryptography import x509

    names: list[str] = ["localhost"]
    ips: list[str] = ["127.0.0.1", "::1"]
    try:
        host = socket.gethostname()
        if host:
            names.append(host)
        for info in socket.getaddrinfo(host, None):
            addr = info[4][0]
            (ips if _is_ip(addr) else names).append(addr)
    except OSError:
        pass
    for h in extra or []:
        (ips if _is_ip(h) else names).append(h)

    entries = []
    seen: set[str] = set()
    for n in names:
        if n and n not in seen:
            seen.add(n)
            entries.append(x509.DNSName(n))
    for ip in ips:
        if ip and ip not in seen:
            seen.add(ip)
            try:
                entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except ValueError:
                pass
    return entries


def _is_ip(value: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False
