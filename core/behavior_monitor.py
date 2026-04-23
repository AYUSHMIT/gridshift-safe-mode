# core/behavior_monitor.py
"""
[Owned by: System Security teammate]

Fuses cryptographic attestation with behavioral consistency to produce
a per-node trust assessment. A node is only TRUSTED when both the
attestation AND the behavioral check pass.
"""
from core.state import TrustLevel, TrustAssessment, VerificationResult


MISMATCH_THRESHOLD_MW = 10.0


class BehaviorMonitor:
    def assess(
        self,
        node_id: str,
        verification: VerificationResult,
        reported_load_mw: float,
        observed_load_mw: float,
    ) -> TrustAssessment:
        mismatch = abs(reported_load_mw - observed_load_mw)
        reasons = []

        if not verification.attested:
            level = TrustLevel.COMPROMISED
            if not verification.signature_ok:
                reasons.append("signature invalid")
            if not verification.pcr_ok:
                reasons.append("PCR mismatch (firmware tampered)")
            if not verification.nonce_ok:
                reasons.append("stale/replayed nonce")
        elif mismatch > MISMATCH_THRESHOLD_MW:
            level = TrustLevel.COMPROMISED
            reasons.append(
                f"reported vs observed mismatch {mismatch:.1f} MW "
                f"> {MISMATCH_THRESHOLD_MW} MW"
            )
        elif mismatch > MISMATCH_THRESHOLD_MW / 2:
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
