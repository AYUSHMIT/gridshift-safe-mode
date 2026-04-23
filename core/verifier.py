# core/verifier.py
"""
[Owned by: Hardware Security teammate]

The Verifier runs on the orchestrator. It registers each controller's
public key and known-good PCR, issues fresh nonces, and validates
signed telemetry packets on three axes:
  1. signature_ok  - packet was signed by the registered private key
  2. pcr_ok        - firmware hash matches the known-good value
  3. nonce_ok      - packet echoes a nonce we issued recently, unused
"""
import time
from dataclasses import dataclass
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from core.attestation import compute_pcr, canonical_payload, fresh_nonce
from core.prover import GOOD_FIRMWARE
from core.state import TelemetryPacket, VerificationResult

NONCE_TTL_SECONDS = 10.0


@dataclass
class NodeRegistration:
    node_id: str
    public_key: Ed25519PublicKey
    known_good_pcr: str


@dataclass
class NonceRecord:
    nonce: str
    issued_at: float


class AttestationVerifier:
    def __init__(self):
        self._registry: dict = {}
        self._outstanding_nonces: dict = {}     # node_id -> NonceRecord

    def register(self, node_id: str, public_key: Ed25519PublicKey):
        known_good = compute_pcr(GOOD_FIRMWARE)
        self._registry[node_id] = NodeRegistration(
            node_id=node_id,
            public_key=public_key,
            known_good_pcr=known_good,
        )

    def issue_nonce(self, node_id: str) -> str:
        n = fresh_nonce()
        self._outstanding_nonces[node_id] = NonceRecord(n, time.time())
        return n

    def verify(self, packet: TelemetryPacket) -> VerificationResult:
        reg = self._registry.get(packet.node_id)
        if reg is None:
            return VerificationResult(False, False, False)

        # 1. Signature check
        payload = canonical_payload(
            packet.node_id, packet.reported_load_mw,
            packet.pcr_quote, packet.nonce, packet.timestamp,
        )
        try:
            reg.public_key.verify(packet.signature, payload)
            sig_ok = True
        except InvalidSignature:
            sig_ok = False

        # 2. PCR check
        pcr_ok = packet.pcr_quote.get("0") == reg.known_good_pcr

        # 3. Nonce freshness check (one-shot, TTL-bounded)
        rec = self._outstanding_nonces.get(packet.node_id)
        nonce_ok = (
            rec is not None
            and rec.nonce == packet.nonce
            and (time.time() - rec.issued_at) <= NONCE_TTL_SECONDS
        )
        # Burn the nonce regardless, so replays fail
        if rec is not None:
            self._outstanding_nonces.pop(packet.node_id, None)

        return VerificationResult(
            signature_ok=sig_ok, pcr_ok=pcr_ok, nonce_ok=nonce_ok
        )


if __name__ == "__main__":
    # Smoke test: clean path, tamper, replay
    from core.attestation import generate_keypair
    from core.prover import Prover

    priv, pub = generate_keypair()
    prover = Prover("BOS-1", priv, load_source=lambda: 12.3)
    verifier = AttestationVerifier()
    verifier.register("BOS-1", pub)

    # Clean path
    n = verifier.issue_nonce("BOS-1")
    pkt = prover.attest(n)
    r = verifier.verify(pkt)
    print(f"clean : sig={r.signature_ok} pcr={r.pcr_ok} "
          f"nonce={r.nonce_ok} attested={r.attested}")

    # Tampered firmware
    prover.tamper_firmware()
    n = verifier.issue_nonce("BOS-1")
    pkt = prover.attest(n)
    r = verifier.verify(pkt)
    print(f"tamper: sig={r.signature_ok} pcr={r.pcr_ok} "
          f"nonce={r.nonce_ok} attested={r.attested}")

    # Replay attack
    prover.restore_firmware()
    n = verifier.issue_nonce("BOS-1")
    pkt = prover.attest(n)
    r1 = verifier.verify(pkt)
    r2 = verifier.verify(pkt)     # same packet again
    print(f"first : attested={r1.attested}")
    print(f"replay: attested={r2.attested}")
