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
from core.state import TickResult
from core.grid_model import BostonGridModel
from core.dc_simulator import DataCenterFleet
from core.verifier import AttestationVerifier
from core.prover import Prover
from core.behavior_monitor import BehaviorMonitor
from core.safety import DecisionEngine, SafetyController
from core.attestation import generate_keypair


class GridShiftOrchestrator:
    def __init__(self, seed: int = 42):
        self.grid = BostonGridModel()
        self.fleet = DataCenterFleet(seed=seed)
        self.verifier = AttestationVerifier()
        self.monitor = BehaviorMonitor()
        self.engine = DecisionEngine()
        self.safety = SafetyController()
        self.provers: dict = {}
        self.tick_num = 0
        self._bootstrap_provers()

    def _bootstrap_provers(self):
        for dc_id, dc in self.fleet.dcs.items():
            priv, pub = generate_keypair()
            self.verifier.register(dc_id, pub)
            self.provers[dc_id] = Prover(
                node_id=dc_id,
                signing_key=priv,
                load_source=(lambda d=dc: d.reported_load_mw()),
            )

    def tick(self) -> TickResult:
        self.tick_num += 1

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
        decisions, safe_mode = self.safety.apply(
            raw_decisions, trust_by_node, self.fleet
        )

        # 4. Execute non-blocked decisions
        for d in decisions:
            if d.action.value == "delay":
                self.fleet.delay(d.job_id)
            elif d.action.value == "migrate" and d.target_dc:
                self.fleet.migrate(d.job_id, d.target_dc)

        # 5. Advance running jobs
        self.fleet.tick()

        return TickResult(
            tick=self.tick_num,
            grid=grid_state,
            assessments=assessments,
            decisions=decisions,
            safe_mode=safe_mode,
        )

    # ---- Demo controls ----
    def trigger_heatwave(self, ticks: int = 60):
        self.grid.start_heatwave(ticks)

    def submit_job_burst(self, n: int = 12):
        self.fleet.submit_burst(n)

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

    def clear_attacks(self):
        for dc_id in self.fleet.dcs:
            self.fleet.disable_lie(dc_id)
            self.provers[dc_id].restore_firmware()
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
