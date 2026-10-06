"""Gas-Network Solver with z-factor corrections (v31).

Steady-state gas network solver for pipeline systems with compressors.
Handles real gas effects via z-factor and compressor affinity laws.

Physics:
  * Pressure-drop in pipes: dp = (2*f*ρ*v²*L) / D, with z-factor corrections
  * Gas density: ρ = (P*M) / (z*R*T)
  * Compressor head: H = a*N² + b*Q + c*Q² (with z-factor efficiency curve)
  * Isothermal flow assumption: T = constant

Canonical units: bar, Sm³/d (gas), days, °C.

Unknowns: node pressures, link flows, compressor speeds (RPM).
"""

import numpy as np
from copy import deepcopy
from typing import Dict, List, Optional, Tuple, Set
from scipy.optimize import least_squares
from dataclasses import dataclass
from collections import defaultdict

# T2.3 integration: import CompressorMap and CompressorAffinityModel
try:
    from optimization.compressor_affinity import CompressorMap, CompressorAffinityModel
except ImportError:
    # Fallback if module not available (backward compatibility)
    CompressorMap = None
    CompressorAffinityModel = None


def create_compressor_station_with_map(
    link_id: str,
    inlet_node_id: str,
    outlet_node_id: str,
    compressor_map: object,  # CompressorMap type
    max_speed_rpm: float = 8500.0,
    min_speed_rpm: float = 3000.0,
) -> 'CompressorStation':
    """Factory helper to create CompressorStation from a CompressorMap.

    Extracts rated parameters from the map to ensure consistency between
    the performance map and station parameters. This is the recommended
    way to set up a compressor with an affinity map.

    T2.3: Enables easy integration of CompressorAffinityModel into networks.

    Args:
        link_id: Unique compressor identifier
        inlet_node_id: Network node ID for inlet
        outlet_node_id: Network node ID for outlet
        compressor_map: CompressorMap instance with performance data
        max_speed_rpm: Maximum operating speed
        min_speed_rpm: Minimum operating speed

    Returns:
        CompressorStation configured with the provided map

    Example:
        >>> from optimization.compressor_affinity import create_axial_compressor_map
        >>> map = create_axial_compressor_map()
        >>> comp = create_compressor_station_with_map("comp1", "inlet", "outlet", map)
    """
    return CompressorStation(
        link_id=link_id,
        inlet_node_id=inlet_node_id,
        outlet_node_id=outlet_node_id,
        rated_flow_sm3d=compressor_map.rated_flow_sm3d,
        rated_head_bar=compressor_map.rated_head_bar,
        rated_speed_rpm=compressor_map.speed_rpm,
        rated_efficiency=compressor_map.get_efficiency(compressor_map.rated_flow_sm3d) or 0.78,
        max_speed_rpm=max_speed_rpm,
        min_speed_rpm=min_speed_rpm,
        compressor_map=compressor_map,  # Store the map for affinity model creation
    )


@dataclass
class GasNodeProperties:
    """Properties of a gas network node."""
    node_id: str
    pressure_bar: Optional[float]  # Fixed pressure or None (unknown)
    temperature_c: float = 15.0
    kind: str = 'facility'  # 'facility', 'well', 'compressor', 'sales_point'
    is_free: bool = False  # True if pressure is unknown


@dataclass
class GasPipeProperties:
    """Properties of a gas pipeline link."""
    link_id: str
    source_id: str
    target_id: str
    length_km: float
    diameter_mm: float
    elevation_gain_m: float = 0.0
    roughness_mm: float = 0.04  # commercial steel
    initial_flow_sm3d: float = 1000.0


@dataclass
class CompressorStation:
    """Centrifugal compressor model with affinity laws.

    T2.3 Enhancement: now supports optional CompressorMap for detailed performance modeling.
    If compressor_map is provided, it enables:
    - Operating-map-based head calculations (more accurate than simplified model)
    - Dynamic surge/runout detection from reference map
    - Efficiency scaling with flow deviation from design point

    If compressor_map is None, falls back to simplified affinity law (backward compatible).
    """
    link_id: str
    inlet_node_id: str
    outlet_node_id: str
    rated_flow_sm3d: float = 1000.0
    rated_head_bar: float = 50.0
    rated_speed_rpm: float = 7000.0
    rated_efficiency: float = 0.78
    max_speed_rpm: float = 8500.0
    min_speed_rpm: float = 3000.0
    compressor_map: Optional[object] = None  # CompressorMap; Optional[CompressorMap] if imports succeed

    def __post_init__(self):
        """Validate consistency between station parameters and map (if provided)."""
        if self.compressor_map is not None:
            # Validate that map rated values match station parameters (within 5%)
            flow_err = abs(self.compressor_map.rated_flow_sm3d - self.rated_flow_sm3d) / self.rated_flow_sm3d
            head_err = abs(self.compressor_map.rated_head_bar - self.rated_head_bar) / self.rated_head_bar
            speed_err = abs(self.compressor_map.speed_rpm - self.rated_speed_rpm) / self.rated_speed_rpm

            if flow_err > 0.05:
                raise ValueError(
                    f"Compressor '{self.link_id}': Map rated flow {self.compressor_map.rated_flow_sm3d} "
                    f"differs from station rated flow {self.rated_flow_sm3d} by >{flow_err*100:.1f}%"
                )
            if head_err > 0.05:
                raise ValueError(
                    f"Compressor '{self.link_id}': Map rated head {self.compressor_map.rated_head_bar} "
                    f"differs from station rated head {self.rated_head_bar} by >{head_err*100:.1f}%"
                )
            if speed_err > 0.05:
                raise ValueError(
                    f"Compressor '{self.link_id}': Map speed {self.compressor_map.speed_rpm} "
                    f"differs from station speed {self.rated_speed_rpm} by >{speed_err*100:.1f}%"
                )


class GasNetworkSolver:
    """Solve steady-state gas network with compressors and real gas effects.

    Attributes:
        nodes: Dict mapping node_id -> GasNodeProperties
        edges: Dict mapping edge_id -> GasPipeProperties
        compressors: Dict mapping compressor_id -> CompressorStation
        pvt_table: PVT table for z-factor lookups (optional)
    """

    def __init__(
        self,
        nodes: Dict[str, GasNodeProperties],
        edges: Dict[str, GasPipeProperties],
        compressors: Optional[Dict[str, CompressorStation]] = None,
        pvt_table=None,
        temperature_c: float = 15.0,
        gas_molecular_weight: float = 17.5,  # North Sea gas typical
    ):
        """Initialize gas network solver.

        Args:
            nodes: Dict of node properties
            edges: Dict of pipe properties
            compressors: Dict of compressor specifications
            pvt_table: PVT table for z-factor (optional; uses z=1 if not provided)
            temperature_c: Isothermal network temperature
            gas_molecular_weight: Average gas molecular weight [kg/kmol]
        """
        self.nodes = nodes
        self.edges = edges
        self.compressors = compressors or {}
        self.pvt_table = pvt_table
        self.temperature_c = temperature_c
        self.M = gas_molecular_weight
        self.R = 8.314  # Universal gas constant [J/(mol·K)]
        self.g = 9.81  # Gravity [m/s²]

        self.scenarios_evaluated = 0
        self.solver_info = {}

        # T2.3 Integration: Initialize CompressorAffinityModel for each compressor with a map
        self.compressor_affinity_models: Dict[str, object] = {}  # Maps comp_id -> CompressorAffinityModel
        self.compressor_has_map: Dict[str, bool] = {}  # Maps comp_id -> bool

        if CompressorAffinityModel is not None:
            for comp_id, comp in self.compressors.items():
                if comp.compressor_map is not None:
                    # Create affinity model from the provided map
                    try:
                        affinity = CompressorAffinityModel(
                            reference_map=comp.compressor_map,
                            reference_speed_rpm=comp.compressor_map.speed_rpm,
                            impeller_diameter_mm=400.0,  # Default; can be customized
                        )
                        self.compressor_affinity_models[comp_id] = affinity
                        self.compressor_has_map[comp_id] = True
                    except Exception as e:
                        # If model creation fails, fall back to simplified
                        self.compressor_has_map[comp_id] = False
                else:
                    self.compressor_has_map[comp_id] = False

    def _get_z_factor(self, p_bar: float) -> float:
        """Get compressibility factor at pressure.

        Args:
            p_bar: Pressure [bar]

        Returns:
            z-factor (dimensionless)
        """
        if self.pvt_table is not None:
            return self.pvt_table.get_z(p_bar, self.temperature_c)
        else:
            # Default: z ≈ 0.9 for North Sea gas at moderate pressures
            return max(0.3, min(1.0, 0.85 + 0.0001 * p_bar))

    def _gas_density(self, p_bar: float) -> float:
        """Calculate gas density at pressure using real gas law.

        ρ = (P * M) / (z * R * T)

        Args:
            p_bar: Absolute pressure [bar]

        Returns:
            Gas density [kg/m³]
        """
        if p_bar <= 0:
            return 0.0

        p_pa = p_bar * 1e5  # Convert bar to Pa
        t_k = self.temperature_c + 273.15  # Convert to Kelvin
        z = self._get_z_factor(p_bar)

        # ρ = (P * M) / (z * R * T); M is in kg/kmol but R is in J/(mol·K),
        # so convert M to kg/mol (÷1000) to get ρ in kg/m³.
        rho = (p_pa * self.M / 1000.0) / (z * self.R * t_k)
        return rho

    def _friction_factor_colebrook(self, re: float, epsilon_d: float) -> float:
        """Colebrook-White friction factor for turbulent flow.

        Args:
            re: Reynolds number
            epsilon_d: Relative roughness (ε/D)

        Returns:
            Friction factor f
        """
        if re < 2300:
            # Laminar: f = 64/Re
            return 64.0 / re
        else:
            # Turbulent: Colebrook-White with iterative solution
            # f = -2 * log10( (ε/D)/3.7 + 2.51/(Re*sqrt(f)) )
            # Iterative approximation:
            f = 0.02  # Initial guess
            for _ in range(5):
                term1 = epsilon_d / 3.7
                term2 = 2.51 / (re * np.sqrt(f))
                f_new = 1.0 / ((-2.0 * np.log10(term1 + term2)) ** 2)
                if abs(f_new - f) < 1e-6:
                    break
                f = f_new
            return max(f, 0.008)

    def _pressure_drop_isothermal(
        self,
        p_inlet_bar: float,
        q_sm3d: float,
        pipe: GasPipeProperties,
    ) -> float:
        """Calculate pressure drop in isothermal pipe.

        Darcy-Weisbach equation with gas density correction:
        Δp = (2*f*ρ*v²*L) / D

        Args:
            p_inlet_bar: Inlet pressure [bar]
            q_sm3d: Flow rate [Sm³/d]
            pipe: Pipe properties

        Returns:
            Pressure drop [bar]
        """
        if q_sm3d <= 0 or p_inlet_bar <= 0:
            return 0.0

        # Convert flow from Sm³/d to m³/s
        q_m3_s = q_sm3d / 86400.0

        # Pipe cross-section
        d_m = pipe.diameter_mm / 1000.0
        a = np.pi * (d_m / 2.0) ** 2  # m²

        # Velocity at inlet conditions
        v = q_m3_s / a if a > 0 else 0.0

        # Density at average pressure (approximate)
        p_avg_bar = p_inlet_bar * 0.85  # Rough estimate of average
        rho = self._gas_density(p_avg_bar)

        # Reynolds number
        mu = 1.2e-5  # Dynamic viscosity of gas at 15°C [Pa·s], roughly constant
        re = (rho * v * d_m) / mu

        # Friction factor
        epsilon = pipe.roughness_mm / 1000.0  # m
        epsilon_d = epsilon / d_m if d_m > 0 else 0.0
        f = self._friction_factor_colebrook(re, epsilon_d)

        # Pressure drop
        if v > 0:
            dp_friction = (2.0 * f * rho * v ** 2 * pipe.length_km * 1000.0) / (d_m * 1e5)
        else:
            dp_friction = 0.0

        # Gravity/elevation effect (small, typically ignored for gas)
        # dp_gravity = (rho * self.g * pipe.elevation_gain_m) / 1e5
        dp_gravity = 0.0  # Simplified

        return dp_friction + dp_gravity

    def _compressor_head_bar(
        self,
        q_sm3d: float,
        p_inlet_bar: float,
        speed_rpm: float,
        compressor: CompressorStation,
        compressor_id: Optional[str] = None,
    ) -> Tuple[float, str]:
        """Calculate compressor discharge head using affinity laws or simplified model.

        T2.3 Integration: Behavior depends on whether CompressorMap is available:
        - WITH map: Uses CompressorAffinityModel.get_performance_at_speed() for accurate operating-map-based calculation
        - WITHOUT map: Falls back to simplified affinity law (backward compatible)

        Args:
            q_sm3d: Inlet volumetric flow [Sm³/d]
            p_inlet_bar: Inlet pressure [bar]
            speed_rpm: Compressor speed [RPM]
            compressor: Compressor specification
            compressor_id: Optional compressor ID for affinity model lookup

        Returns:
            Tuple[float, str]: (head_bar, status_code)
            status_code: 'OK' (operating normally)
                        'SURGE' (flow below safe limit)
                        'RUNOUT' (flow above safe limit)
                        'OFF' (speed or flow is zero)
        """
        if q_sm3d <= 0 or speed_rpm <= 0:
            return 0.0, 'OFF'

        # Try to use CompressorAffinityModel if available
        if compressor_id and compressor_id in self.compressor_affinity_models:
            affinity = self.compressor_affinity_models[compressor_id]
            h, eff, p, status = affinity.get_performance_at_speed(speed_rpm, q_sm3d)

            if status == 'SURGE':
                # Graceful handling: return minimal head instead of crashing
                # Allows solver to continue iterating while alerting on surge
                return 0.0, 'SURGE'
            elif status == 'RUNOUT':
                return 0.0, 'RUNOUT'
            elif status == 'OFF':
                return 0.0, 'OFF'
            else:  # status == 'OK'
                return max(0.0, h), 'OK'

        # Fallback: simplified affinity law (backward compatible)
        flow_ratio = q_sm3d / compressor.rated_flow_sm3d
        speed_ratio = speed_rpm / compressor.rated_speed_rpm

        # Operating envelope check
        # Surge occurs at low flow; runout at high flow
        surge_threshold = 0.5  # Surge at 50% of rated (typical)
        runout_threshold = 1.3  # Runout at 130% of rated

        # Shutdown: stall/surge protection (simple model)
        if flow_ratio < surge_threshold:
            return 0.0, 'SURGE'  # Compressor stalls (no head)

        # Runout protection: flow > runout point drops efficiency sharply
        if flow_ratio > runout_threshold:
            return 0.0, 'RUNOUT'  # Choked (no additional pressure rise)

        # Head calculation using affinity laws:
        # H = H_rated * (N/N_rated)² * f(Q/Q_rated)
        # f() models the head-flow characteristic
        # For a typical centrifugal: f(x) = 1.5 - 0.5*x (roughly)

        h_relative = 1.5 - 0.5 * flow_ratio
        h = compressor.rated_head_bar * (speed_ratio ** 2) * h_relative

        return max(0.0, h), 'OK'

    def _compressor_discharge_pressure(
        self,
        p_inlet_bar: float,
        q_sm3d: float,
        speed_rpm: float,
        compressor: CompressorStation,
        compressor_id: Optional[str] = None,
    ) -> Tuple[float, str]:
        """Calculate discharge pressure after compressor.

        T2.3: Now returns both discharge pressure and operating status.

        p_discharge = p_inlet + head_bar

        Args:
            p_inlet_bar: Inlet pressure [bar]
            q_sm3d: Volumetric flow [Sm³/d]
            speed_rpm: Compressor speed [RPM]
            compressor: Compressor specification
            compressor_id: Optional compressor ID for affinity model lookup

        Returns:
            Tuple[float, str]: (discharge_pressure_bar, status_code)
        """
        head, status = self._compressor_head_bar(q_sm3d, p_inlet_bar, speed_rpm, compressor, compressor_id)
        discharge_p = p_inlet_bar + head
        return discharge_p, status

    def solve_isothermal(
        self,
        compressor_speeds: Optional[Dict[str, float]] = None,
        max_iterations: int = 30,
        tolerance: float = 0.1,  # bar
        verbose: bool = False,
    ) -> Tuple[Dict[str, float], Dict[str, float], Dict]:
        """Solve gas network using iterative isothermal method.

        Algorithm:
        1. Assume initial pressures at nodes
        2. Calculate flow using pressure gradient
        3. Update pressures via pressure-drop equations
        4. Repeat until convergence

        Args:
            compressor_speeds: Dict mapping compressor_id -> speed_rpm (optional)
            max_iterations: Maximum iterations
            tolerance: Pressure convergence tolerance [bar]
            verbose: Print iteration details

        Returns:
            (node_pressures, link_flows, info) tuple
        """
        self.scenarios_evaluated += 1

        # Initialize from fixed pressures
        pressures = {}
        for node_id, node in self.nodes.items():
            if node.pressure_bar is not None:
                pressures[node_id] = node.pressure_bar
            else:
                # Heuristic: assume intermediate pressure
                pressures[node_id] = 30.0

        # Initialize flows
        flows = {}
        for edge_id, pipe in self.edges.items():
            flows[edge_id] = pipe.initial_flow_sm3d

        # Compressor speeds (default to rated)
        comp_speeds = compressor_speeds or {}
        for comp_id, comp in self.compressors.items():
            if comp_id not in comp_speeds:
                comp_speeds[comp_id] = comp.rated_speed_rpm

        if verbose:
            print(f"[GN] Starting isothermal solve with {len(self.nodes)} nodes, {len(self.edges)} pipes")

        # Iterative solve
        residuals_history = []
        for iteration in range(max_iterations):
            p_old = pressures.copy()

            # Update flows based on pressure gradients (simplified: Q ∝ ΔP^0.5)
            for edge_id, pipe in self.edges.items():
                source_p = pressures.get(pipe.source_id, 20.0)
                target_p = pressures.get(pipe.target_id, 10.0)

                # Pressure-driven flow (simplified model)
                if source_p > target_p:
                    # Flow from high to low pressure
                    dp = max(source_p - target_p, 0.1)
                    # Q ≈ K * sqrt(Δp) where K depends on pipe
                    k = 50.0  # Flow conductance [Sm³/d / sqrt(bar)]
                    flows[edge_id] = k * np.sqrt(dp)
                else:
                    flows[edge_id] = 0.1

            # Update pressures using pressure-drop equations
            for edge_id, pipe in self.edges.items():
                source_p = pressures.get(pipe.source_id, 20.0)
                q = flows[edge_id]

                # Calculate pressure drop
                dp = self._pressure_drop_isothermal(source_p, q, pipe)

                # Update target pressure
                new_target_p = source_p - dp
                target_id = pipe.target_id

                # Only update if this node is free (not fixed)
                if self.nodes[target_id].pressure_bar is None:
                    pressures[target_id] = max(new_target_p, 1.0)

            # Apply compressor effects (T2.3: now tracks status per compressor)
            compressor_status = {}  # Map comp_id -> status_code
            for comp_id, comp in self.compressors.items():
                inlet_p = pressures.get(comp.inlet_node_id, 20.0)
                speed = comp_speeds.get(comp_id, comp.rated_speed_rpm)
                q = flows.get(comp_id, 500.0)  # Use link flow if available

                discharge_p, status = self._compressor_discharge_pressure(inlet_p, q, speed, comp, comp_id)
                compressor_status[comp_id] = status  # Store status for reporting

                # Update outlet pressure
                if self.nodes[comp.outlet_node_id].pressure_bar is None:
                    pressures[comp.outlet_node_id] = discharge_p

            # Check convergence
            max_residual = max(
                abs(pressures.get(nid, 0) - p_old.get(nid, 0))
                for nid in pressures
            )
            residuals_history.append(max_residual)

            if verbose and (iteration + 1) % 5 == 0:
                print(f"  Iter {iteration+1}: max Δp = {max_residual:.4f} bar")

            if max_residual < tolerance:
                if verbose:
                    print(f"[GN] Converged in {iteration+1} iterations")
                break

        # Compile info (T2.3: now includes compressor status tracking)
        surge_warnings = [cid for cid, s in compressor_status.items() if s == 'SURGE']
        runout_warnings = [cid for cid, s in compressor_status.items() if s == 'RUNOUT']

        info = {
            'success': residuals_history[-1] < tolerance if residuals_history else False,
            'iterations': len(residuals_history),
            'final_residual_bar': residuals_history[-1] if residuals_history else 0.0,
            'residual_history': residuals_history,
            'solver_mode': 'isothermal_iterative',
            'z_factor_correction': self.pvt_table is not None,
            'compressor_count': len(self.compressors),
            'compressor_status': compressor_status,  # T2.3: Operating status per compressor
            'surge_warnings': surge_warnings,  # T2.3: List of compressors at surge
            'runout_warnings': runout_warnings,  # T2.3: List of compressors at runout
            'affinity_model_count': len(self.compressor_affinity_models),  # T2.3: How many use affinity maps
            'message': f'Converged in {len(residuals_history)} iterations' if residuals_history[-1] < tolerance else 'Did not converge'
        }

        return pressures, flows, info

    def solve_network(
        self,
        nodes_dict: List[Dict],
        edges_dict: List[Dict],
        compressor_dict: Optional[List[Dict]] = None,
        **kwargs
    ) -> Tuple[Dict, Dict, Dict, Dict]:
        """Convenience interface matching steady_state.solve_network signature.

        Converts dict-based input to internal types and solves.

        Args:
            nodes_dict: List of node dicts with 'id', 'pressure_bar', 'kind'
            edges_dict: List of edge dicts with 'id', 'source', 'target', 'params'
            compressor_dict: Optional list of compressor dicts

        Returns:
            (pressures, flows, info, details) tuple for compatibility
        """
        # Convert to internal types
        nodes = {}
        for n in nodes_dict:
            node_id = n['id']
            nodes[node_id] = GasNodeProperties(
                node_id=node_id,
                pressure_bar=n.get('pressure_bar'),
                temperature_c=n.get('temperature_c', 15.0),
                kind=n.get('kind', 'facility'),
            )

        edges = {}
        for e in edges_dict:
            edge_id = e['id']
            params = e.get('params', {})
            edges[edge_id] = GasPipeProperties(
                link_id=edge_id,
                source_id=e.get('source'),
                target_id=e.get('target'),
                length_km=params.get('length_km', 10.0),
                diameter_mm=params.get('diameter_mm', 300.0),
                initial_flow_sm3d=params.get('initial_flow_sm3d', 1000.0),
            )

        compressors = {}
        if compressor_dict:
            for c in compressor_dict:
                comp_id = c['id']
                params = c.get('params', {})
                compressors[comp_id] = CompressorStation(
                    link_id=comp_id,
                    inlet_node_id=params.get('inlet_node_id'),
                    outlet_node_id=params.get('outlet_node_id'),
                    rated_flow_sm3d=params.get('rated_flow_sm3d', 1000.0),
                    rated_head_bar=params.get('rated_head_bar', 50.0),
                    rated_speed_rpm=params.get('rated_speed_rpm', 7000.0),
                )

        # Install the converted network so solve_isothermal() sees it
        # (previously the solver kept its constructor-time, possibly empty, network).
        self.nodes = nodes
        self.edges = edges
        self.compressors = compressors

        # Solve
        pressures, flows, info = self.solve_isothermal(**kwargs)

        # Format details for compatibility
        details = {
            'node_pressures': pressures,
            'link_flows': flows,
            'z_factor_used': self.pvt_table is not None,
        }

        return pressures, flows, info, details


def solve_gas_network(
    nodes: List[Dict],
    edges: List[Dict],
    compressors: Optional[List[Dict]] = None,
    pvt_table=None,
    temperature_c: float = 15.0,
    verbose: bool = False,
) -> Tuple[Dict, Dict, Dict, Dict]:
    """Convenience function to solve gas network.

    Args:
        nodes: Network nodes with pressures and kinds
        edges: Pipeline connections with dimensions
        compressors: Optional compressor specifications
        pvt_table: Optional PVT table for z-factor
        temperature_c: Isothermal network temperature
        verbose: Print progress

    Returns:
        (pressures, flows, info, details) tuple
    """
    solver = GasNetworkSolver(
        nodes={},
        edges={},
        pvt_table=pvt_table,
        temperature_c=temperature_c,
    )
    return solver.solve_network(nodes, edges, compressors, verbose=verbose)
