# core/attestation.py
"""
[Owned by: Hardware Security teammate]

Shared cryptographic primitives used by both the prover (DC controller side)
and the verifier (orchestrator side).
"""
import hashlib
import json
import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization


def generate_keypair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv, pub


def priv_to_bytes(priv: Ed25519PrivateKey) -> bytes:
    return priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def pub_to_bytes(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def bytes_to_priv(b: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b)


def bytes_to_pub(b: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(b)


def compute_pcr(firmware_bytes: bytes) -> str:
    """
    Simulated PCR (Platform Configuration Register): SHA-256 over the
    'firmware image'. In real hardware this would be a TPM PCR populated
    during measured boot.
    """
    return hashlib.sha256(firmware_bytes).hexdigest()


def canonical_payload(
    node_id: str,
    reported_load_mw: float,
    pcr_quote: dict,
    nonce: str,
    timestamp: float,
) -> bytes:
    """
    Deterministic JSON serialization used as the byte string that gets signed.
    Both prover and verifier MUST produce identical bytes for the same inputs.
    """
    obj = {
        "node_id": node_id,
        "reported_load_mw": round(reported_load_mw, 4),
        "pcr_quote": pcr_quote,
        "nonce": nonce,
        "timestamp": round(timestamp, 3),
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def fresh_nonce() -> str:
    return secrets.token_hex(16)
