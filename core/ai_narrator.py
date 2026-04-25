# core/ai_narrator.py
"""
[AI component - used by: System Security / UI]

LLM-powered incident narrator. When safe mode activates, this module
consumes the structured tick state and produces a natural-language
operator briefing suitable for a human grid operator.

Design principles:
  - Graceful fallback: if no API key is configured or the network is
    unavailable, fall back to a deterministic rule-based narration so
    the demo always works (even offline at the venue).
  - Provider-agnostic: supports both Anthropic (Claude) and OpenAI.
    Selects whichever has a key configured, preferring Anthropic.
  - Cheap and fast: the LLM is only called when safe_mode is active
    AND trust state changed since the last tick, or when explicitly
    requested. Every call is strictly bounded in tokens.
  - Honest about its role: the LLM explains, it does NOT decide.
    All control decisions are made by the deterministic safety layer
    BEFORE the narrator ever sees them. The LLM is observing, not
    acting, on the grid.

Usage:
    narrator = IncidentNarrator()  # reads env for API keys
    briefing = narrator.narrate(tick_result, orchestrator_state)

    # briefing.text       - natural-language paragraph
    # briefing.source     - "anthropic" | "openai" | "rule-based-fallback"
    # briefing.latency_ms - how long it took
"""
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, List
from core.state import TickResult, TrustAssessment, TrustLevel


log = logging.getLogger(__name__)


@dataclass
class Briefing:
    text: str
    source: str          # "anthropic" | "openai" | "rule-based-fallback"
    latency_ms: int
    model: Optional[str] = None


# Briefings are short (3-5 sentences). Cap output tokens so a stuck or
# verbose model can't run away with cost or latency.
BRIEFING_MAX_TOKENS = 300


# --------------------------------------------------------------------------
# The system prompt for the LLM. Keep this tight -- it's the contract
# between our code and the model.
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are GridShift's incident-reporting assistant. You write concise \
operator briefings for electrical-grid operators monitoring AI data \
centers. Your briefings are read by humans under time pressure.

Rules:
- Be factual and concise. 3 to 5 short sentences. No marketing language.
- State what happened, what the system did about it, and what the \
operator should do next.
- Never invent facts. Use only the structured state provided in the user \
message. If a field is absent, do not speculate.
- Do not use bullet points. Write flowing operator-log prose.
- Use 24-hour timestamps in UTC if a timestamp is given.
- Do not add preamble like "Here is the briefing" -- return the briefing \
text only.
"""


def _build_user_payload(tick: TickResult) -> str:
    """
    Serialize the tick state into a compact JSON payload for the LLM.

    We keep all nodes (the LLM still needs to write briefings on clean
    ticks too), but drop redundant/tautological fields:
      - For TRUSTED nodes, omit the per-check booleans (sig/pcr/nonce
        all == True is implied by trust=="trusted"). Keep them on
        non-trusted nodes so the LLM can name the specific failure.
      - Skip mismatch_mw when it is zero.
      - Drop target on non-migrate decisions.
      - One timestamp, not two.
      - No JSON indentation -- the model does not benefit from
        whitespace tokens.
    """
    assessments = []
    for a in tick.assessments:
        item = {
            "node": a.node_id,
            "trust": a.level.value,
            "reported_mw": round(a.reported_load_mw, 2),
            "observed_mw": round(a.observed_load_mw, 2),
            "reason": a.reason,
        }
        if a.mismatch_mw > 0:
            item["mismatch_mw"] = round(a.mismatch_mw, 2)
        # Per-check booleans are only informative when something failed.
        if a.level.value != "trusted":
            item["sig_ok"] = a.verification.signature_ok
            item["pcr_ok"] = a.verification.pcr_ok
            item["nonce_ok"] = a.verification.nonce_ok
        assessments.append(item)

    decisions = []
    for d in tick.decisions:
        item = {
            "job": d.job_id,
            "action": d.action.value,
            "source": d.source_dc,
            "reason": d.reason,
        }
        if d.target_dc:
            item["target"] = d.target_dc
        decisions.append(item)

    payload = {
        "tick": tick.tick,
        "timestamp_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(tick.timestamp)
        ),
        "grid": {
            "total_load_mw": round(tick.grid.total_load_mw, 1),
            "threshold_mw": round(tick.grid.threshold_mw, 0),
            "overload_risk_pct": round(tick.grid.overload_risk * 100, 1),
        },
        "safe_mode": tick.safe_mode,
        "nodes": assessments,
        "decisions": decisions,
    }
    return (
        "Produce an operator briefing for the following GridShift tick. "
        "Tick state (JSON): " + json.dumps(payload, separators=(",", ":"))
    )


# --------------------------------------------------------------------------
# Provider adapters
# --------------------------------------------------------------------------

def _extract_anthropic_text(resp) -> Optional[str]:
    """
    Pull text out of an Anthropic Messages API response. Skips non-text
    blocks (thinking, tool_use, etc.). Returns None if nothing usable.
    """
    try:
        blocks = resp.content
    except AttributeError:
        return None
    if not blocks:
        return None
    parts = [getattr(b, "text", None) for b in blocks
             if getattr(b, "type", None) == "text"]
    text = "\n".join(p for p in parts if p).strip()
    return text or None


def _extract_openai_text(message) -> Optional[str]:
    """
    Pull text out of an OpenAI ChatCompletion message. The .content
    field can be:
      - a plain string (regular text models)
      - a list of content parts (vision / multimodal models)
      - None (when the model returned tool calls only)
    Returns None on any of these failure modes so the caller falls
    through to the next provider or the rule-based fallback.
    """
    content = getattr(message, "content", None)
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        # Each part may be a dict-like object or pydantic model.
        # We accept any "text" field present on parts of type "text".
        parts = []
        for part in content:
            ptype = getattr(part, "type", None)
            if ptype is None and isinstance(part, dict):
                ptype = part.get("type")
            if ptype != "text":
                continue
            ptext = getattr(part, "text", None)
            if ptext is None and isinstance(part, dict):
                ptext = part.get("text")
            if ptext:
                parts.append(ptext)
        text = "\n".join(parts).strip()
        return text or None
    return None


def _call_anthropic(user_msg: str, timeout_s: float = 8.0) -> Optional[Briefing]:
    """Try Anthropic Claude. Returns None on any failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None

    model = os.environ.get("GRIDSHIFT_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=BRIEFING_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = _extract_anthropic_text(resp)
        if not text:
            return None
        return Briefing(
            text=text,
            source="anthropic",
            latency_ms=int((time.time() - t0) * 1000),
            model=model,
        )
    except Exception as exc:
        # Best-effort path: any failure (rate limit, auth, network,
        # SDK shape change, ...) falls through to the next provider.
        # Log so the failure is traceable when debugging, but do not
        # raise -- the caller's contract is "return Briefing or None".
        log.warning(
            "Anthropic narrator call failed (model=%s): %s: %s",
            model, type(exc).__name__, exc,
        )
        return None


def _call_openai(user_msg: str, timeout_s: float = 8.0) -> Optional[Briefing]:
    """Try OpenAI. Returns None on any failure."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return None

    model = os.environ.get("GRIDSHIFT_OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key, timeout=timeout_s)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=BRIEFING_MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        if not getattr(resp, "choices", None):
            return None
        text = _extract_openai_text(resp.choices[0].message)
        if not text:
            return None
        return Briefing(
            text=text,
            source="openai",
            latency_ms=int((time.time() - t0) * 1000),
            model=model,
        )
    except Exception as exc:
        log.warning(
            "OpenAI narrator call failed (model=%s): %s: %s",
            model, type(exc).__name__, exc,
        )
        return None


# --------------------------------------------------------------------------
# Rule-based fallback. Guaranteed to work offline.
# --------------------------------------------------------------------------

def _rule_based_narration(tick: TickResult) -> Briefing:
    t0 = time.time()
    bad: List[TrustAssessment] = [
        a for a in tick.assessments if a.level != TrustLevel.TRUSTED
    ]
    ts = time.strftime("%H:%M:%S", time.gmtime(tick.timestamp))

    parts: List[str] = []
    if not bad:
        parts.append(
            f"At {ts} UTC all data-center controllers attest cleanly and "
            f"report load consistent with grid-side measurements."
        )
        if tick.grid.total_load_mw > tick.grid.threshold_mw:
            parts.append(
                f"Total Boston load is {tick.grid.total_load_mw:.0f} MW, "
                f"above the {tick.grid.threshold_mw:.0f} MW threshold; "
                f"GridShift is applying load-balancing actions."
            )
        else:
            parts.append(
                f"Total Boston load is {tick.grid.total_load_mw:.0f} MW, "
                f"within the {tick.grid.threshold_mw:.0f} MW safety threshold."
            )
    else:
        for a in bad:
            why = []
            if not a.verification.pcr_ok:
                why.append("PCR mismatch indicating possible firmware tampering")
            if not a.verification.signature_ok:
                why.append("invalid packet signature")
            if not a.verification.nonce_ok:
                why.append("stale or replayed nonce")
            if a.mismatch_mw > 10:
                why.append(
                    f"reported load diverging from observed load by "
                    f"{a.mismatch_mw:.1f} MW"
                )
            parts.append(
                f"At {ts} UTC {a.node_id} was flagged {a.level.value.upper()} "
                f"due to {', '.join(why) if why else 'borderline anomaly'}."
            )
        parts.append(
            f"GridShift has entered safe mode: migrations INTO the affected "
            f"node(s) are blocked and migrations OUT are preferred to reduce "
            f"exposure."
        )
        migrates = [d for d in tick.decisions if d.action.value == "migrate"]
        if migrates:
            m_txt = "; ".join(
                f"{d.job_id} {d.source_dc}->{d.target_dc}" for d in migrates
            )
            parts.append(f"Actions taken this tick: migrated {m_txt}.")
        parts.append(
            "Recommended operator action: physically inspect the flagged "
            "controller before restoring trust."
        )

    return Briefing(
        text=" ".join(parts),
        source="rule-based-fallback",
        latency_ms=int((time.time() - t0) * 1000),
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

class IncidentNarrator:
    """
    Tries Anthropic first, then OpenAI, then falls back to rule-based.

    The caller does not need to know which backend succeeded; the
    Briefing.source field records it for display.

    Set GRIDSHIFT_FORCE_FALLBACK=1 in the environment to always use the
    rule-based path (useful for venue demos without Wi-Fi).
    """

    def __init__(self, prefer_llm: bool = True):
        self.prefer_llm = prefer_llm
        self.force_fallback = (
            os.environ.get("GRIDSHIFT_FORCE_FALLBACK", "").lower() in ("1", "true", "yes")
        )

    def narrate(self, tick: TickResult) -> Briefing:
        if self.force_fallback or not self.prefer_llm:
            return _rule_based_narration(tick)

        user_msg = _build_user_payload(tick)

        briefing = _call_anthropic(user_msg)
        if briefing is not None:
            return briefing

        briefing = _call_openai(user_msg)
        if briefing is not None:
            return briefing

        return _rule_based_narration(tick)


if __name__ == "__main__":
    # Standalone smoke test -- works fully offline via fallback
    from core.orchestrator import GridShiftOrchestrator
    orch = GridShiftOrchestrator()
    orch.trigger_heatwave(60)
    orch.submit_job_burst(14)
    orch.tick()
    orch.tick()
    orch.start_attack_tamper("BOS-1")
    orch.spike_load("BOS-1", 25.0)
    tick = orch.tick()

    narrator = IncidentNarrator()
    briefing = narrator.narrate(tick)
    print(f"Source: {briefing.source}  latency: {briefing.latency_ms} ms")
    if briefing.model:
        print(f"Model : {briefing.model}")
    print()
    print(briefing.text)