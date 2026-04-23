# core/orchestrator.py
"""
[Owned by: System Security teammate - but this is the integration point
for the whole team]

The GridShiftOrchestrator is the heart of the system. Every tick it:
  1. Advances the grid and DC simulation
  2. Challenges each controller with a fresh nonce
  3. Verifies the returned signed telemetry (signature + PCR + nonce)
  4. Compares reported vs observed load (behavioral check)
  5. Plans decisions, filters them through safe mode
  6. Executes the surviving decisions
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
        """Create a keypair per DC and hand the public half to the verifier."""
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

        # 2. Challenge each controller and verify the response
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

        # 3. Plan actions, then filter through safe mode
        raw_decisions = self.engine.plan(grid_state, self.fleet)
        trust_levels = [a.level for a in assessments]
        decisions, safe_mode = self.safety.apply(raw_decisions, trust_levels)

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
        """Scene 2: controller lies about its load. Attestation still passes."""
        self.fleet.enable_lie(dc_id, delta)

    def start_attack_tamper(self, dc_id: str = "BOS-1"):
        """Scene 2 alt: firmware tampered. Attestation fails (PCR mismatch)."""
        self.provers[dc_id].tamper_firmware()

    def clear_attacks(self):
        for dc_id in self.fleet.dcs:
            self.fleet.disable_lie(dc_id)
            self.provers[dc_id].restore_firmware()


if __name__ == "__main__":
    # End-to-end smoke test
    orch = GridShiftOrchestrator()
    orch.trigger_heatwave(60)
    orch.submit_job_burst(14)

    print("--- Normal operation ---")
    for _ in range(3):
        r = orch.tick()
        print(f"tick {r.tick}: total={r.grid.total_load_mw:.1f} "
              f"safe_mode={r.safe_mode} decisions={len(r.decisions)}")
        for a in r.assessments:
            print(f"  {a.node_id}: {a.level.value}  {a.reason}")

    print("\n--- Behavioral attack: BOS-1 lies ---")
    orch.start_attack_lying("BOS-1", 16.0)
    for _ in range(2):
        r = orch.tick()
        print(f"tick {r.tick}: total={r.grid.total_load_mw:.1f} "
              f"safe_mode={r.safe_mode}")
        for a in r.assessments:
            print(f"  {a.node_id}: {a.level.value}  {a.reason}")
        for d in r.decisions:
            print(f"  -> {d.job_id}: {d.action.value} ({d.reason})")

    print("\n--- Firmware attack: BOS-1 tampered ---")
    orch.clear_attacks()
    orch.start_attack_tamper("BOS-1")
    r = orch.tick()
    print(f"tick {r.tick}: safe_mode={r.safe_mode}")
    for a in r.assessments:
        print(f"  {a.node_id}: {a.level.value}  {a.reason}")
