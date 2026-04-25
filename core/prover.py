# core/prover.py
"""
[Owned by: Hardware Security teammate]

The Prover lives on each DC controller. It performs a 'measured boot'
(simulated by hashing a firmware byte string), holds a sealed signing key
(simulated by a Python object), and produces signed telemetry packets
in response to verifier nonces.
"""
import time
from typing import Callable
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core.attestation import compute_pcr, canonical_payload
from core.state import TelemetryPacket


# The "firmware image" each controller is supposed to be running.
# In production this would be flash contents; here it's a byte string.
GOOD_FIRMWARE = b"GRIDSHIFT_CONTROLLER_FW_v1.0.0::trusted-build"
TAMPERED_FIRMWARE = b"GRIDSHIFT_CONTROLLER_FW_v1.0.0::MALICIOUS_PATCH"


class Prover:
    def __init__(
        self,
        node_id: str,
        signing_key: Ed25519PrivateKey,
        load_source: Callable[[], float],
        firmware: bytes = GOOD_FIRMWARE,
    ):
        self.node_id = node_id
        self._signing_key = signing_key
        self._load_source = load_source
        self._firmware = firmware
        self._pcr = compute_pcr(self._firmware)     # "measured boot"

    def tamper_firmware(self):
        """Simulate an attacker swapping the firmware."""
        self._firmware = TAMPERED_FIRMWARE
        self._pcr = compute_pcr(self._firmware)

    def restore_firmware(self):
        self._firmware = GOOD_FIRMWARE
        self._pcr = compute_pcr(self._firmware)

    def firmware_matches_known_good(self) -> bool:
        """
        Public self-check: does this prover's currently-loaded firmware
        match the known-good baseline? This is the single source of truth
        for "is this device tampered" -- callers should use this rather
        than reading the internal PCR directly.

        Note: this is a self-attestation check. In a hostile environment
        a compromised prover could lie about this; the orchestrator's
        verifier is what actually enforces trust. This method exists so
        the UI / monitoring code can describe device state without
        needing to re-implement PCR comparison.
        """
        return self._pcr == compute_pcr(GOOD_FIRMWARE)

    def attest(self, nonce: str) -> TelemetryPacket:
        reported = self._load_source()
        ts = time.time()
        pcr_quote = {"0": self._pcr}
        payload = canonical_payload(
            self.node_id, reported, pcr_quote, nonce, ts
        )
        sig = self._signing_key.sign(payload)
        return TelemetryPacket(
            node_id=self.node_id,
            reported_load_mw=reported,
            pcr_quote=pcr_quote,
            nonce=nonce,
            timestamp=ts,
            signature=sig,
        )