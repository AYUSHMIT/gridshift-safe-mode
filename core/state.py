# core/state.py
"""
Shared data contracts for the whole GridShift system.
Build this together in hour 0-2 so all four teammates speak the same types.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
import time


class TrustLevel(str, Enum):
    TRUSTED = "trusted"
    SUSPICIOUS = "suspicious"
    COMPROMISED = "compromised"


class JobPriority(str, Enum):
    CRITICAL = "critical"        # cannot be delayed or migrated
    FLEXIBLE = "flexible"        # can be delayed
    MIGRATABLE = "migratable"    # can be moved to another DC


class ActionType(str, Enum):
    RUN = "run"
    DELAY = "delay"
    MIGRATE = "migrate"
    BLOCK = "block"              # forced by safe mode


@dataclass
class Job:
    job_id: str
    priority: JobPriority
    power_mw: float
    duration_ticks: int
    home_dc: str
    # --- added for CCNC eval (Arash/DC) ---
    region: str = ""                       # region of the DC currently hosting it
    submit_tick: int = 0                   # when the job entered the system
    base_duration_ticks: int = 0           # original runtime, for SLA reference
    completed_tick: Optional[int] = None   # set when retired
    delayed_until_tick: Optional[int] = None
    # in-flight migration state (k-tick migration cost)
    migrating: bool = False
    migration_remaining: int = 0
    migration_target: Optional[str] = None
    migration_source: Optional[str] = None

    def sla_deadline(self, slack: float) -> int:
        """Ideal completion tick allowing a fractional slack on runtime."""
        return self.submit_tick + int(self.base_duration_ticks * (1.0 + slack))


@dataclass
class GridState:
    base_load_mw: float           # Boston base load
    dc_load_mw: float             # aggregate DC load
    threshold_mw: float           # alert threshold (e.g., 900)
    heatwave_multiplier: float    # 1.0-1.3

    @property
    def total_load_mw(self) -> float:
        return self.base_load_mw * self.heatwave_multiplier + self.dc_load_mw

    @property
    def overload_risk(self) -> float:
        return max(0.0, (self.total_load_mw - self.threshold_mw) / self.threshold_mw)


@dataclass
class TelemetryPacket:
    node_id: str
    reported_load_mw: float
    pcr_quote: dict               # {pcr_index: hex_hash}
    nonce: str
    timestamp: float
    signature: bytes


@dataclass
class VerificationResult:
    signature_ok: bool
    pcr_ok: bool
    nonce_ok: bool

    @property
    def attested(self) -> bool:
        return self.signature_ok and self.pcr_ok and self.nonce_ok


@dataclass
class TrustAssessment:
    node_id: str
    verification: VerificationResult
    reported_load_mw: float
    observed_load_mw: float
    mismatch_mw: float
    level: TrustLevel
    reason: str


@dataclass
class Decision:
    job_id: str
    action: ActionType
    source_dc: str
    target_dc: Optional[str] = None
    reason: str = ""


@dataclass
class TickResult:
    tick: int
    grid: GridState
    assessments: List[TrustAssessment]
    decisions: List[Decision]
    safe_mode: bool
    trapped_load_mw: float = 0.0   # load on untrusted nodes this tick (exposure)
    migration_candidates_considered: int = 0
    candidates_with_trusted_feasible_destination: int = 0
    candidates_blocked_insufficient_destination_capacity: int = 0
    migration_feasibility_rate: float = 0.0
    trusted_residual_headroom_mw: float = 0.0
    trusted_destinations_with_positive_headroom: int = 0
    scheduler_migration_decisions_raw: int = 0
    scheduler_migration_decisions_to_trusted_capacity_feasible: int = 0
    safety_allowed_migrations: int = 0
    safety_explicit_block_migrations: int = 0
    safety_raw_migrations_removed_or_converted: int = 0
    safety_blocked_migrations: int = 0
    executed_migrations_this_tick: int = 0
    executed_corrective_migrations_this_tick: int = 0
    grid_threshold_exceeded: bool = False
    timestamp: float = field(default_factory=time.time)
