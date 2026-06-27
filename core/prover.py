# core/prover.py
"""
[Owned by: Hardware Security teammate]

The Prover lives on each DC controller. It performs a 'measured boot'
(simulated by hashing a firmware byte string), holds a sealed signing key
(simulated by a Python object), and produces signed telemetry packets
in response to verifier nonces.

Attack hooks are exposed for replay / nonce-staleness and key-compromise
experiments used by the adversary and Monte-Carlo harness.
"""
import time
from typing import Callable, Optional
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
        replay_nonce: bool = False,
        key_compromise: bool = False,
    ):
        self.node_id = node_id
        self._signing_key = signing_key
        self._load_source = load_source
        self._firmware = firmware
        self._pcr = compute_pcr(self._firmware)     # "measured boot"

        # Attack hooks.
        self._replay_nonce = replay_nonce
        self._key_compromise = key_compromise
        self._last_packet: Optional[TelemetryPacket] = None

    def configure_attacks(
        self,
        firmware_tamper: Optional[bool] = None,
        replay_nonce: Optional[bool] = None,
        key_compromise: Optional[bool] = None,
    ):
        """
        Stable interface for adversary / experiment code.

        firmware_tamper=True:
            device firmware changes, so PCR differs from known-good.

        replay_nonce=True:
            prover reuses the previous signed packet, causing a stale nonce.

        key_compromise=True:
            attacker can sign a forged quote that reports the known-good PCR.
        """
        if firmware_tamper is not None:
            if firmware_tamper:
                self.tamper_firmware()
            else:
                self.restore_firmware()

        if replay_nonce is not None:
            self._replay_nonce = replay_nonce
            if not replay_nonce:
                self._last_packet = None

        if key_compromise is not None:
            self._key_compromise = key_compromise

    def enable_replay_nonce(self):
        """Enable stale-nonce / replay behavior."""
        self._replay_nonce = True

    def disable_replay_nonce(self):
        """Disable stale-nonce / replay behavior."""
        self._replay_nonce = False
        self._last_packet = None

    def compromise_key(self):
        """Enable stolen-key / forged-quote behavior."""
        self._key_compromise = True

    def restore_key(self):
        """Disable stolen-key behavior."""
        self._key_compromise = False

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
        # Replay / nonce-staleness attack:
        # return the previous signed packet instead of answering the fresh nonce.
        if self._replay_nonce and self._last_packet is not None:
            return self._last_packet

        reported = self._load_source()
        ts = time.time()

        # Key-compromise attack:
        # attacker can sign a forged quote claiming the known-good PCR.
        if self._key_compromise:
            pcr_quote = {"0": compute_pcr(GOOD_FIRMWARE)}
        else:
            pcr_quote = {"0": self._pcr}

        payload = canonical_payload(
            self.node_id, reported, pcr_quote, nonce, ts
        )
        sig = self._signing_key.sign(payload)

        packet = TelemetryPacket(
            node_id=self.node_id,
            reported_load_mw=reported,
            pcr_quote=pcr_quote,
            nonce=nonce,
            timestamp=ts,
            signature=sig,
        )

        self._last_packet = packet
        return packet
