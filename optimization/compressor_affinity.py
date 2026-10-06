"""Compressor Affinity Laws and Performance Mapping (v31).

Variable-speed centrifugal compressor modeling with surge/runout envelope,
efficiency curves, and polytropic calculations.

Canonical units: bar, Sm³/d (gas), RPM, kW, °C.

Theory:
  Affinity Laws for dynamic similarity:
  H₂/H₁ = (N₂/N₁)² * (D₂/D₁)³                    [head scaling]
  P₂/P₁ = (N₂/N₁)³ * (D₂/D₁)⁵ * (ρ₂/ρ₁)         [power scaling]
  Surge Line: minimum stable operating point (typically 50-70% of BEP)
  Runout: maximum stable point (typically 100-130% of BEP)
"""

import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from scipy.interpolate import interp1d


@dataclass
class CompressorMap:
    """Manufacturer compressor performance map (single speed)."""
    speed_rpm: float
    rated_flow_sm3d: float
    rated_head_bar: float

    # Operating points (flow, head, efficiency) - sorted by flow
    design_flows: List[float]  # Sm³/d
    design_heads: List[float]  # bar
    design_efficiencies: List[float]  # fraction (0.0-1.0)

    # Envelope points
    surge_flow_sm3d: float  # Minimum stable flow
    runout_flow_sm3d: float  # Maximum stable flow

    def __post_init__(self):
        """Validate and create interpolators."""
        if not (len(self.design_flows) == len(self.design_heads) == len(self.design_efficiencies)):
            raise ValueError("design_flows, design_heads, design_efficiencies must have same length")

        if len(self.design_flows) < 2:
            raise ValueError("Need at least 2 design points")

        # Create interpolators
        self._flow_arr = np.array(self.design_flows)
        self._head_arr = np.array(self.design_heads)
        self._eff_arr = np.array(self.design_efficiencies)

        # Check surge < rated < runout
        if not (self.surge_flow_sm3d < self.rated_flow_sm3d < self.runout_flow_sm3d):
            raise ValueError(
                f"Envelope violated: surge {self.surge_flow_sm3d} < "
                f"rated {self.rated_flow_sm3d} < runout {self.runout_flow_sm3d}"
            )

    def get_head(self, flow_sm3d: float) -> Optional[float]:
        """Interpolate head at flow."""
        if flow_sm3d < self.design_flows[0] or flow_sm3d > self.design_flows[-1]:
            return None
        return float(np.interp(flow_sm3d, self._flow_arr, self._head_arr))

    def get_efficiency(self, flow_sm3d: float) -> Optional[float]:
        """Interpolate efficiency at flow."""
        if flow_sm3d < self.design_flows[0] or flow_sm3d > self.design_flows[-1]:
            return None
        return float(np.interp(flow_sm3d, self._flow_arr, self._eff_arr))


class CompressorAffinityModel:
    """Multi-speed compressor model using affinity laws.

    Attributes:
        reference_map: CompressorMap at reference speed
        reference_speed_rpm: Speed of reference map
        impeller_diameter_mm: Physical impeller diameter (for geometry scaling)
    """

    def __init__(
        self,
        reference_map: CompressorMap,
        reference_speed_rpm: float,
        impeller_diameter_mm: float = 400.0,
    ):
        """Initialize affinity model.

        Args:
            reference_map: Performance map at reference speed
            reference_speed_rpm: The speed at which reference_map is valid
            impeller_diameter_mm: Impeller diameter for geometry scaling
        """
        if reference_speed_rpm != reference_map.speed_rpm:
            raise ValueError(
                f"reference_speed_rpm {reference_speed_rpm} must match "
                f"reference_map.speed_rpm {reference_map.speed_rpm}"
            )

        self.reference_map = reference_map
        self.reference_speed_rpm = reference_speed_rpm
        self.impeller_diameter_mm = impeller_diameter_mm

        self.scenarios_evaluated = 0

    def scale_head(self, head_ref: float, speed_ref: float, speed_new: float) -> float:
        """Scale head using affinity law: H ∝ (N/N_ref)²

        Args:
            head_ref: Head at reference speed [bar]
            speed_ref: Reference speed [RPM]
            speed_new: Target speed [RPM]

        Returns:
            Scaled head [bar]
        """
        if speed_ref <= 0 or head_ref <= 0:
            return 0.0

        speed_ratio = speed_new / speed_ref
        return head_ref * (speed_ratio ** 2)

    def scale_flow(self, flow_ref: float, speed_ref: float, speed_new: float) -> float:
        """Scale volumetric flow using affinity law: Q ∝ (N/N_ref)

        Args:
            flow_ref: Flow at reference speed [Sm³/d]
            speed_ref: Reference speed [RPM]
            speed_new: Target speed [RPM]

        Returns:
            Scaled flow [Sm³/d]
        """
        if speed_ref <= 0 or flow_ref <= 0:
            return 0.0

        speed_ratio = speed_new / speed_ref
        return flow_ref * speed_ratio

    def scale_power(self, power_ref: float, speed_ref: float, speed_new: float, density_ratio: float = 1.0) -> float:
        """Scale power using affinity law: P ∝ (N/N_ref)³ * (ρ/ρ_ref)

        Args:
            power_ref: Power at reference speed [kW]
            speed_ref: Reference speed [RPM]
            speed_new: Target speed [RPM]
            density_ratio: Density ratio (ρ_new/ρ_ref), default 1.0 for isothermal

        Returns:
            Scaled power [kW]
        """
        if speed_ref <= 0 or power_ref <= 0:
            return 0.0

        speed_ratio = speed_new / speed_ref
        return power_ref * (speed_ratio ** 3) * density_ratio

    def scale_efficiency(self, flow_ref: float, flow_new: float) -> float:
        """Estimate efficiency change with flow (simplified).

        Real compressor efficiency changes due to Reynolds number and
        flow coefficient changes. This is a simplified model that
        assumes efficiency stays roughly constant near design point
        and drops off rapidly near surge/runout.

        Args:
            flow_ref: Reference flow (typically design point) [Sm³/d]
            flow_new: New operating flow [Sm³/d]

        Returns:
            Efficiency multiplier relative to design (0.0-1.0)
        """
        if flow_ref <= 0:
            return 0.0

        flow_ratio = flow_new / flow_ref

        # Efficiency peaking curve (roughly parabolic near BEP)
        # Full efficiency at 85-105% of design
        # Drops off towards surge and runout

        if 0.85 <= flow_ratio <= 1.05:
            # Near design point: high efficiency
            return 0.98 + 0.01 * np.cos(np.pi * (flow_ratio - 0.95) / 0.1)
        elif flow_ratio < 0.85:
            # Approaching surge: efficiency drops sharply
            slope = (0.98 - 0.7) / 0.35  # 0.8 efficiency drop over 35% flow range
            return max(0.7, 0.98 - slope * (0.85 - flow_ratio))
        else:
            # Approaching runout: efficiency drops gradually
            slope = (0.98 - 0.75) / 0.25  # 0.23 drop over 25% flow range
            return max(0.75, 0.98 - slope * (flow_ratio - 1.05))

    def get_performance_at_speed(
        self,
        target_speed_rpm: float,
        inlet_flow_sm3d: float,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
        """Get compressor performance (head, efficiency, power) at target speed and flow.

        Args:
            target_speed_rpm: Operating speed [RPM]
            inlet_flow_sm3d: Inlet volumetric flow [Sm³/d]

        Returns:
            (head_bar, efficiency, power_kw, status) tuple
            status: 'OK', 'SURGE', 'RUNOUT', or 'OFF'
        """
        self.scenarios_evaluated += 1

        if target_speed_rpm <= 0 or inlet_flow_sm3d <= 0:
            return None, None, None, 'OFF'

        # Scale surge and runout to target speed
        surge_scaled = self.scale_flow(
            self.reference_map.surge_flow_sm3d,
            self.reference_speed_rpm,
            target_speed_rpm
        )
        runout_scaled = self.scale_flow(
            self.reference_map.runout_flow_sm3d,
            self.reference_speed_rpm,
            target_speed_rpm
        )

        # Check operating envelope
        if inlet_flow_sm3d < surge_scaled:
            return None, None, None, 'SURGE'
        if inlet_flow_sm3d > runout_scaled:
            return None, None, None, 'RUNOUT'

        # Scale design point head to target speed
        rated_head_scaled = self.scale_head(
            self.reference_map.rated_head_bar,
            self.reference_speed_rpm,
            target_speed_rpm
        )

        # Get efficiency at reference speed (interpolate from map)
        eff_ref = self.reference_map.get_efficiency(self.reference_map.rated_flow_sm3d)
        if eff_ref is None:
            eff_ref = 0.78

        # Estimate efficiency at target flow
        eff_multiplier = self.scale_efficiency(self.reference_map.rated_flow_sm3d, inlet_flow_sm3d)
        efficiency = eff_ref * eff_multiplier

        # Calculate power (simplified: P = Q*H*ρ*g / η)
        # For isothermal gas: estimate power from head and flow
        # P [kW] ≈ (Q [Sm³/d] * H [bar] * 1.0) / (3600 * η)
        if efficiency > 0:
            power_kw = (inlet_flow_sm3d * rated_head_scaled * 1.0) / (3600.0 * efficiency)
        else:
            power_kw = 0.0

        return rated_head_scaled, efficiency, power_kw, 'OK'

    def operating_map(
        self,
        target_speed_rpm: float,
        flow_range: Tuple[float, float] = (200.0, 3000.0),
        n_points: int = 50,
    ) -> Dict:
        """Generate compressor operating map at target speed.

        Args:
            target_speed_rpm: Speed at which to generate map
            flow_range: (min_flow, max_flow) Sm³/d
            n_points: Number of operating points

        Returns:
            Dict with keys:
            - 'flows': List of flow values
            - 'heads': List of head values
            - 'efficiencies': List of efficiency values
            - 'powers': List of power values
            - 'statuses': List of status strings
        """
        flows = np.linspace(flow_range[0], flow_range[1], n_points)
        heads = []
        efficiencies = []
        powers = []
        statuses = []

        for q in flows:
            h, eff, p, status = self.get_performance_at_speed(target_speed_rpm, q)
            heads.append(h if h is not None else 0.0)
            efficiencies.append(eff if eff is not None else 0.0)
            powers.append(p if p is not None else 0.0)
            statuses.append(status)

        return {
            'speed_rpm': target_speed_rpm,
            'flows': flows.tolist(),
            'heads': heads,
            'efficiencies': efficiencies,
            'powers': powers,
            'statuses': statuses,
        }

    def optimization_envelope(
        self,
        speed_range: Tuple[float, float],
        n_speeds: int = 10,
    ) -> List[Dict]:
        """Generate optimization envelope (surge and runout lines).

        Args:
            speed_range: (min_speed, max_speed) RPM
            n_speeds: Number of speed lines to generate

        Returns:
            List of dicts with surge and runout curves for each speed
        """
        results = []
        speeds = np.linspace(speed_range[0], speed_range[1], n_speeds)

        for speed in speeds:
            surge_flow = self.scale_flow(
                self.reference_map.surge_flow_sm3d,
                self.reference_speed_rpm,
                speed
            )
            runout_flow = self.scale_flow(
                self.reference_map.runout_flow_sm3d,
                self.reference_speed_rpm,
                speed
            )
            surge_head = self.scale_head(
                self.reference_map.rated_head_bar * 1.2,  # Surge head typically higher
                self.reference_speed_rpm,
                speed
            )

            results.append({
                'speed_rpm': float(speed),
                'surge_flow_sm3d': surge_flow,
                'surge_head_bar': surge_head,
                'runout_flow_sm3d': runout_flow,
            })

        return results


def create_axial_compressor_map() -> CompressorMap:
    """Factory: Create a typical axial compressor map (North Sea gas).

    Returns:
        CompressorMap with representative performance data
    """
    return CompressorMap(
        speed_rpm=7000.0,
        rated_flow_sm3d=1500.0,
        rated_head_bar=42.0,
        design_flows=[600.0, 900.0, 1200.0, 1500.0, 1800.0, 2000.0],
        design_heads=[55.0, 52.0, 48.0, 42.0, 35.0, 25.0],
        design_efficiencies=[0.72, 0.78, 0.82, 0.80, 0.75, 0.65],
        surge_flow_sm3d=450.0,
        runout_flow_sm3d=2200.0,
    )


def create_centrifugal_compressor_map() -> CompressorMap:
    """Factory: Create a typical centrifugal compressor map (high pressure).

    Returns:
        CompressorMap with representative performance data
    """
    return CompressorMap(
        speed_rpm=8500.0,
        rated_flow_sm3d=800.0,
        rated_head_bar=65.0,
        design_flows=[300.0, 500.0, 700.0, 800.0, 900.0, 1000.0],
        design_heads=[75.0, 72.0, 68.0, 65.0, 61.0, 55.0],
        design_efficiencies=[0.70, 0.76, 0.80, 0.78, 0.75, 0.68],
        surge_flow_sm3d=200.0,
        runout_flow_sm3d=1150.0,
    )


if __name__ == '__main__':
    # Example usage
    ref_map = create_axial_compressor_map()
    comp = CompressorAffinityModel(ref_map, 7000.0)

    # Get performance at 70% speed
    h, eff, p, status = comp.get_performance_at_speed(4900.0, 1050.0)
    print(f"At 4900 RPM, 1050 Sm³/d: H={h:.1f} bar, η={eff:.1%}, P={p:.1f} kW, status={status}")
