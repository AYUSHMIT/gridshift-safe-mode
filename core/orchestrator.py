# core/orchestrator.py
"""
[Owned by: System Security teammate - but this is the integration point
for the whole team]

Every tick:
  1. Advance the grid + DC simulation
  2. Challenge each controller with a fresh nonce
  3. Verify the signed telemetry (signature + PCR + nonce)
  4. Compare reported vs observed load (behavioral check)
  5. Plan decisions, then run them through the safety controller
     (directional policy + observed-load override)
  6. Execute the surviving decisions
"""
from typing import Optional
from core.state import TickResult, TrustLevel, JobPriority, ActionType
from core.grid_model import BostonGridModel, GridConfig
from core.dc_simulator import DataCenterFleet
from core.verifier import AttestationVerifier
from core.prover import Prover
from core.behavior_monitor import BehaviorMonitor
from core.safety import DecisionEngine, SafetyController
from core.attestation import generate_keypair
from core.config import SimConfig
from core.adversary import configure_adversary


class GridShiftOrchestrator:
    def __init__(
        self,
        seed: int = 42,
        config: Optional[SimConfig] = None,
        grid_config: Optional[GridConfig] = None,
        grid_trace_source=None,
        apply_heatwave_to_trace: bool = False,
    ):
        self.cfg = config or SimConfig(seed=seed)
        self.grid = BostonGridModel(
            config=grid_config,
            trace_source=grid_trace_source,
            apply_heatwave_to_trace=apply_heatwave_to_trace,
        )
        self.fleet = DataCenterFleet(seed=seed, config=self.cfg)
        self.verifier = AttestationVerifier()
        self.monitor = BehaviorMonitor(detector_mode=self.cfg.detector_mode)
        self.engine = DecisionEngine()
        self.safety = SafetyController(policy=self.cfg.policy)
        self.provers: dict = {}
        self.tick_num = 0
        self.last_adversary_events: list[str] = []
        self._bootstrap_provers()

    def _bootstrap_provers(self):
        for dc_id, dc in self.fleet.dcs.items():
            priv, pub = generate_keypair()
            self.verifier.register(dc_id, pub)
            prover = Prover(
                node_id=dc_id,
                signing_key=priv,
                load_source=(lambda d=dc: d.reported_load_mw()),
            )
            self.provers[dc_id] = prover

    def tick(self) -> TickResult:
        self.tick_num += 1
        self.last_adversary_events = configure_adversary(
            self, self.cfg, self.tick_num
        )

        # 1. Evolve the simulation
        self.fleet.place_pending()
        self.grid.set_dc_load(self.fleet.total_true_load_mw())
        grid_state = self.grid.tick()

        # 2. Challenge each controller and verify
        assessments = []
        for dc_id, prover in self.provers.items():
            nonce = self.verifier.issue_nonce(dc_id)
            packet = prover.attest(nonce)
            verif = self.verifier.verify(packet)
            observed = self.fleet.dcs[dc_id].observed_load_mw()
            assessments.append(self.monitor.assess(
                node_id=dc_id,
                verification=verif,
                reported_load_mw=packet.reported_load_mw,
                observed_load_mw=observed,
            ))

        # 3. Plan + safety filter (per-node trust map)
        raw_decisions = self.engine.plan(grid_state, self.fleet)
        trust_by_node = {a.node_id: a.level for a in assessments}
        migration_feasibility = self._migration_feasibility_diagnostics(
            trust_by_node
        )
        actuation_diagnostics = self._actuation_diagnostics_before_safety(
            raw_decisions,
            trust_by_node,
            grid_state,
        )
        decisions, safe_mode = self.safety.apply(
            raw_decisions, trust_by_node, self.fleet
        )
        actuation_diagnostics.update(
            self._actuation_diagnostics_after_safety(raw_decisions, decisions)
        )

        # 4. Execute non-blocked decisions
        executed_migrations_this_tick = 0
        executed_corrective_migrations_this_tick = 0
        for d in decisions:
            if d.action.value == "delay":
                self.fleet.delay(d.job_id)
            elif d.action.value == "migrate" and d.target_dc:
                migrated = self.fleet.migrate(d.job_id, d.target_dc)
                if migrated:
                    executed_migrations_this_tick += 1
                    # Empirical corrective-action proxy: successful migrations
                    # that drain untrusted sources, originate from observed-load
                    # unwind logic, or occur while the grid threshold is already
                    # exceeded. This observes behavior; it does not prove cause.
                    if (
                        trust_by_node.get(d.source_dc) != TrustLevel.TRUSTED
                        or "UNWIND:" in d.reason
                        or actuation_diagnostics["grid_threshold_exceeded"]
                    ):
                        executed_corrective_migrations_this_tick += 1
        actuation_diagnostics["executed_migrations_this_tick"] = (
            executed_migrations_this_tick
        )
        actuation_diagnostics["executed_corrective_migrations_this_tick"] = (
            executed_corrective_migrations_this_tick
        )

        # 4b. Measure exposure: load left on untrusted nodes after mitigation.
        bad_nodes = {n for n, lvl in trust_by_node.items()
                     if lvl != TrustLevel.TRUSTED}
        trapped_load_mw = self.fleet.load_on_nodes(bad_nodes)

        # 5. Advance running jobs
        self.fleet.tick()

        return TickResult(
            tick=self.tick_num,
            grid=grid_state,
            assessments=assessments,
            decisions=decisions,
            safe_mode=safe_mode,
            trapped_load_mw=trapped_load_mw,
            **migration_feasibility,
            **actuation_diagnostics,
        )

    def _migration_feasibility_diagnostics(self, trust_by_node: dict) -> dict:
        candidates = 0
        feasible = 0

        for src_id, dc in self.fleet.dcs.items():
            if trust_by_node.get(src_id) == TrustLevel.TRUSTED:
                continue
            for job in dc.running_jobs:
                if job.priority != JobPriority.MIGRATABLE:
                    continue
                candidates += 1
                for target_id, target_dc in self.fleet.dcs.items():
                    if target_id == src_id:
                        continue
                    if trust_by_node.get(target_id) != TrustLevel.TRUSTED:
                        continue
                    if target_dc.can_accept(job):
                        feasible += 1
                        break

        blocked = candidates - feasible
        rate = feasible / candidates if candidates else 0.0
        return {
            "migration_candidates_considered": candidates,
            "candidates_with_trusted_feasible_destination": feasible,
            "candidates_blocked_insufficient_destination_capacity": blocked,
            "migration_feasibility_rate": rate,
        }

    def _actuation_diagnostics_before_safety(
        self,
        raw_decisions: list,
        trust_by_node: dict,
        grid_state,
    ) -> dict:
        raw_migrations = [
            decision
            for decision in raw_decisions
            if decision.action == ActionType.MIGRATE
        ]
        trusted_capacity_feasible = 0
        for decision in raw_migrations:
            if decision.target_dc is None:
                continue
            if trust_by_node.get(decision.target_dc) != TrustLevel.TRUSTED:
                continue
            target = self.fleet.dcs.get(decision.target_dc)
            job = self._running_job(decision.job_id)
            if target is not None and job is not None and target.can_accept(job):
                trusted_capacity_feasible += 1

        # Keep this consistent with DataCenter.can_accept(), which is based on
        # true load rather than reported/attested load.
        trusted_headroom = 0.0
        trusted_destinations = 0
        for dc_id, dc in self.fleet.dcs.items():
            if trust_by_node.get(dc_id) != TrustLevel.TRUSTED:
                continue
            residual = max(0.0, dc.capacity_mw - dc.true_load_mw())
            trusted_headroom += residual
            if residual > 0:
                trusted_destinations += 1

        return {
            "trusted_residual_headroom_mw": float(trusted_headroom),
            "trusted_destinations_with_positive_headroom": int(
                trusted_destinations
            ),
            "scheduler_migration_decisions_raw": len(raw_migrations),
            "scheduler_migration_decisions_to_trusted_capacity_feasible": (
                trusted_capacity_feasible
            ),
            "grid_threshold_exceeded": (
                grid_state.total_load_mw > grid_state.threshold_mw
            ),
        }

    def _actuation_diagnostics_after_safety(
        self,
        raw_decisions: list,
        decisions: list,
    ) -> dict:
        safety_allowed_migrations = sum(
            1 for decision in decisions
            if decision.action == ActionType.MIGRATE
        )
        allowed_raw_migration_keys = {
            (decision.job_id, decision.source_dc, decision.target_dc)
            for decision in decisions
            if decision.action == ActionType.MIGRATE
        }
        raw_migration_keys = {
            (decision.job_id, decision.source_dc, decision.target_dc)
            for decision in raw_decisions
            if decision.action == ActionType.MIGRATE
        }
        converted_or_removed_migrations = len(
            raw_migration_keys - allowed_raw_migration_keys
        )
        explicit_blocks = sum(
            1 for decision in decisions
            if decision.action == ActionType.BLOCK
        )
        return {
            "safety_allowed_migrations": safety_allowed_migrations,
            "safety_explicit_block_migrations": explicit_blocks,
            "safety_raw_migrations_removed_or_converted": (
                converted_or_removed_migrations
            ),
            # Compatibility alias: raw scheduler migrations that did not remain
            # allowed migrations after safety filtering. Explicit BLOCK
            # decisions are often the representation of the same removed raw
            # migration, so summing both fields would double-count those cases.
            "safety_blocked_migrations": converted_or_removed_migrations,
        }

    def _running_job(self, job_id: str):
        for dc in self.fleet.dcs.values():
            for job in dc.running_jobs:
                if job.job_id == job_id:
                    return job
        return None

    # ---- Demo controls ----
    def trigger_heatwave(self, ticks: int = 60):
        self.grid.start_heatwave(ticks)

    def submit_job_burst(self, n: int = 12):
        self.fleet.submit_burst(n)

    def submit_jobs(self, job_specs):
        self.fleet.submit_jobs(job_specs)

    def start_attack_lying(self, dc_id: str = "BOS-1", delta: float = 16.0):
        """Behavioral attack: controller lies about its load."""
        self.fleet.enable_lie(dc_id, delta)

    def stop_attack_lying(self, dc_id: str = "BOS-1"):
        """Stop just the lying attack on a node, leaving other attacks alone."""
        self.fleet.disable_lie(dc_id)

    def start_attack_tamper(self, dc_id: str = "BOS-1"):
        """Firmware attack: PCR no longer matches known-good."""
        self.provers[dc_id].tamper_firmware()

    def spike_load(self, dc_id: str = "BOS-1", extra_mw: float = 20.0):
        """
        Supervisor-scenario attack (step 2): after triggering a false
        attestation failure, the adversary inflates real load. Used to
        show that the refined safe mode UNWINDS rather than freezes.
        """
        self.fleet.spike(dc_id, extra_mw)

    def start_attack_replay(self, dc_id: str = "BOS-1"):
        """Replay attack: prover reuses a previous signed packet / stale nonce."""
        self.provers[dc_id].enable_replay_nonce()

    def stop_attack_replay(self, dc_id: str = "BOS-1"):
        """Stop replay / stale-nonce attack on a node."""
        self.provers[dc_id].disable_replay_nonce()

    def start_attack_key_compromise(self, dc_id: str = "BOS-1"):
        """Key-compromise attack: attacker can sign a forged known-good quote."""
        self.provers[dc_id].compromise_key()

    def stop_attack_key_compromise(self, dc_id: str = "BOS-1"):
        """Stop key-compromise attack on a node."""
        self.provers[dc_id].restore_key()

    def clear_attacks(self):
        for dc_id in self.fleet.dcs:
            self.fleet.disable_lie(dc_id)
            self.provers[dc_id].restore_firmware()
            self.provers[dc_id].disable_replay_nonce()
            self.provers[dc_id].restore_key()
            self.fleet.clear_spike(dc_id)


if __name__ == "__main__":
    # End-to-end smoke test covering the supervisor-scenario attack
    orch = GridShiftOrchestrator()
    orch.trigger_heatwave(60)
    orch.submit_job_burst(14)

    print("--- Normal operation ---")
    for _ in range(2):
        r = orch.tick()
        print(f"tick {r.tick}: total={r.grid.total_load_mw:.1f} "
              f"safe_mode={r.safe_mode} decisions={len(r.decisions)}")

    print("\n--- Supervisor scenario: false attestation failure + load spike ---")
    orch.start_attack_tamper("BOS-1")
    orch.spike_load("BOS-1", 25.0)
    for _ in range(2):
        r = orch.tick()
        print(f"tick {r.tick}: total={r.grid.total_load_mw:.1f} "
              f"safe_mode={r.safe_mode}")
        for a in r.assessments:
            print(f"  {a.node_id}: {a.level.value}  {a.reason}")
        for d in r.decisions:
            tgt = d.target_dc or "-"
            print(f"  -> {d.job_id}: {d.action.value} "
                  f"src={d.source_dc} tgt={tgt} ({d.reason})")
