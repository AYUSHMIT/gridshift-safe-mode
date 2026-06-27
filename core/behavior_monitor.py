# core/behavior_monitor.py
"""
[Owned by: System Security teammate]

Produces a per-node trust assessment from:
  1. cryptographic attestation: signature / PCR / nonce
  2. behavioral consistency: reported load vs observed load

The detector can run in three modes for experiments:
  - "attestation-only": use only signature/PCR/nonce checks
  - "behavior-only": use only reported-vs-observed load mismatch
  - "fusion": require both attestation and behavior to be acceptable
"""
from core.state import TrustLevel, TrustAssessment, VerificationResult


MISMATCH_THRESHOLD_MW = 10.0
VALID_DETECTOR_MODES = {"attestation-only", "behavior-only", "fusion"}


class BehaviorMonitor:
    def __init__(
        self,
        detector_mode: str = "fusion",
        mismatch_threshold_mw: float = MISMATCH_THRESHOLD_MW,
    ):
        if detector_mode not in VALID_DETECTOR_MODES:
            raise ValueError(
                f"Invalid detector_mode={detector_mode!r}. "
                f"Expected one of {sorted(VALID_DETECTOR_MODES)}."
            )
        self.detector_mode = detector_mode
        self.mismatch_threshold_mw = mismatch_threshold_mw

    def _attestation_failure_reasons(
        self, verification: VerificationResult
    ) -> list:
        reasons = []
        if not verification.signature_ok:
            reasons.append("signature invalid")
        if not verification.pcr_ok:
            reasons.append("PCR mismatch (firmware tampered)")
        if not verification.nonce_ok:
            reasons.append("stale/replayed nonce")
        return reasons

    def assess(
        self,
        node_id: str,
        verification: VerificationResult,
        reported_load_mw: float,
        observed_load_mw: float,
    ) -> TrustAssessment:
        mismatch = abs(reported_load_mw - observed_load_mw)
        reasons = []

        attestation_failed = not verification.attested
        behavior_compromised = mismatch > self.mismatch_threshold_mw
        behavior_suspicious = mismatch > self.mismatch_threshold_mw / 2

        # Mode 1: attestation-only
        # Only signature/PCR/nonce matter. Behavioral mismatch is ignored.
        if self.detector_mode == "attestation-only":
            if attestation_failed:
                level = TrustLevel.COMPROMISED
                reasons.extend(self._attestation_failure_reasons(verification))
            else:
                level = TrustLevel.TRUSTED
                reasons.append("attestation checks passed")
            reasons.append("behavior ignored in attestation-only mode")

        # Mode 2: behavior-only
        # Only reported-vs-observed mismatch matters. Attestation is ignored.
        elif self.detector_mode == "behavior-only":
            if behavior_compromised:
                level = TrustLevel.COMPROMISED
                reasons.append(
                    f"reported vs observed mismatch {mismatch:.1f} MW "
                    f"> {self.mismatch_threshold_mw} MW"
                )
            elif behavior_suspicious:
                level = TrustLevel.SUSPICIOUS
                reasons.append(f"borderline mismatch {mismatch:.1f} MW")
            else:
                level = TrustLevel.TRUSTED
                reasons.append("behavior check passed")
            reasons.append("attestation ignored in behavior-only mode")

        # Mode 3: fusion
        # A node is trusted only when attestation passes and behavior is consistent.
        else:
            if attestation_failed:
                level = TrustLevel.COMPROMISED
                reasons.extend(self._attestation_failure_reasons(verification))
            elif behavior_compromised:
                level = TrustLevel.COMPROMISED
                reasons.append(
                    f"reported vs observed mismatch {mismatch:.1f} MW "
                    f"> {self.mismatch_threshold_mw} MW"
                )
            elif behavior_suspicious:
                level = TrustLevel.SUSPICIOUS
                reasons.append(f"borderline mismatch {mismatch:.1f} MW")
            else:
                level = TrustLevel.TRUSTED
                reasons.append("all checks passed")

        return TrustAssessment(
            node_id=node_id,
            verification=verification,
            reported_load_mw=reported_load_mw,
            observed_load_mw=observed_load_mw,
            mismatch_mw=mismatch,
            level=level,
            reason="; ".join(reasons),
        )
