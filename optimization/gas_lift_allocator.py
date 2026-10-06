"""Gas-Lift Allocation Optimizer (v31).

Dynamically allocates available lift gas among producers to maximize total oil production.
Uses marginal efficiency analysis (Δoil per Δgas) to rebalance lift allocation iteratively.

Canonical units: bar, m³/d (liquid), Sm³/d (gas), days.
"""

from copy import deepcopy
from typing import Dict, List, Optional, Tuple
from collections import defaultdict


class GasLiftAllocator:
    """Optimize gas-lift injection across producers.

    Attributes:
        nodes: Graph node list (wells, tanks, facilities)
        edges: Graph edge list (connections)
        wells: List of producer well nodes with gas_lift capability
        total_lift_m3d: Total lift gas available [Sm³/d]
        min_lift_step_m3d: Allocation step size for marginal measurement [Sm³/d]
        max_iterations: Maximum allocation iterations
        solve_func: Callable to solve the network (warm-start capable)
    """

    def __init__(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        total_lift_m3d: float = 500.0,
        min_lift_step_m3d: float = 10.0,
        max_iterations: int = 20,
        solve_func=None,
    ):
        """Initialize gas-lift allocator.

        Args:
            nodes: Network node list
            edges: Network edge list
            total_lift_m3d: Total gas-lift available [Sm³/d]
            min_lift_step_m3d: Step size for marginal measurement [Sm³/d]
            max_iterations: Max reallocation iterations
            solve_func: Network solver callable (default: imported from solver.steady_state)
        """
        self.nodes = nodes
        self.edges = edges
        self.total_lift_m3d = max(total_lift_m3d, 0.0)
        self.min_lift_step_m3d = max(min_lift_step_m3d, 1.0)
        self.max_iterations = max_iterations

        # Filter producer wells with gas-lift capability
        self.wells = [
            n for n in nodes
            if n.get('kind') == 'well' and
               (n.get('params') or {}).get('lift_type', '').lower() == 'gas_lift' and
               (n.get('params') or {}).get('available', True)
        ]

        # Import solver if not provided
        if solve_func is None:
            try:
                from solver.steady_state import solve_network
                self.solve_func = solve_network
            except ImportError:
                # Fallback: mock solver for testing
                self.solve_func = self._mock_solve
        else:
            self.solve_func = solve_func

        self.allocation_history = []
        self.marginal_efficiency_curve = {}

    def _mock_solve(self, nodes, edges):
        """Mock solver for testing (placeholder)."""
        details = {
            n['id']: {
                'liquid_rate_m3d': (n.get('params') or {}).get('pi_m3d_bar', 10.0) *
                                   max(1.0 - (n.get('params') or {}).get('gas_lift_injection_sm3d', 0) / 1000, 0.5)
            }
            for n in nodes if n.get('kind') == 'well'
        }
        info = {'residual': 0, 'constraints': []}
        return (None, None, info, details)

    def _total_oil_rate(self, details: Dict) -> float:
        """Sum liquid rate across all wells."""
        return sum(v.get('liquid_rate_m3d', 0.0) for v in details.values())

    def _solve_and_get_rate(self, nodes: List[Dict]) -> Tuple[Optional[float], bool]:
        """Solve network and return total oil rate.

        Returns:
            (total_rate, success) tuple
        """
        try:
            result = self.solve_func(nodes, deepcopy(self.edges))
            if result is None:
                return None, False

            # result format: (pressures, flows, info, details)
            p, q, info, details = result
            rate = self._total_oil_rate(details)
            return rate, True
        except Exception as e:
            # Solver failed or ill-conditioned
            return None, False

    def _get_well_rate(self, nodes: List[Dict], well_id: str) -> Optional[float]:
        """Get individual well liquid rate."""
        try:
            result = self.solve_func(nodes, deepcopy(self.edges))
            if result is None:
                return None

            p, q, info, details = result
            for well_node in nodes:
                if well_node.get('id') == well_id and well_node.get('kind') == 'well':
                    return details.get(well_id, {}).get('liquid_rate_m3d', 0.0)
            return None
        except Exception:
            return None

    def measure_marginal_efficiency(
        self,
        well_id: str,
        current_lift_m3d: float,
        nodes_current: List[Dict],
    ) -> Optional[float]:
        """Measure marginal oil response to lift increment for one well.

        Args:
            well_id: Well identifier
            current_lift_m3d: Current lift allocation [Sm³/d]
            nodes_current: Network nodes with current allocation

        Returns:
            Marginal efficiency [m³/d oil per Sm³/d lift] or None if measurement fails
        """
        # Increase lift by min_lift_step for this well
        nodes_with_lift = deepcopy(nodes_current)
        for n in nodes_with_lift:
            if n.get('id') == well_id and n.get('kind') == 'well':
                params = n.setdefault('params', {})
                params['gas_lift_injection_sm3d'] = current_lift_m3d + self.min_lift_step_m3d

        rate_with_extra, success_with = self._solve_and_get_rate(nodes_with_lift)
        if not success_with or rate_with_extra is None:
            return None

        # Baseline rate (current lift)
        rate_baseline, success_base = self._solve_and_get_rate(nodes_current)
        if not success_base or rate_baseline is None:
            return None

        marginal = (rate_with_extra - rate_baseline) / self.min_lift_step_m3d
        return max(marginal, 0.0)  # Clamp to non-negative

    def allocate(self, verbose: bool = False) -> Dict:
        """Allocate lift to maximize total oil production.

        Uses iterative marginal-efficiency approach:
        1. Measure marginal response for each well
        2. Allocate lift to highest-marginal well
        3. Repeat until capacity exhausted or convergence

        Args:
            verbose: Print iteration details

        Returns:
            Dict with keys:
            - 'success': bool
            - 'allocation': {well_id: lift_m3d}
            - 'total_lift_used': float [Sm³/d]
            - 'total_oil_rate': float [m³/d]
            - 'baseline_rate': float [m³/d] (no lift)
            - 'improvement_percent': float
            - 'marginal_efficiency_history': List of (lift_allocated, marginal_dict)
            - 'message': str
        """
        if not self.wells:
            return {
                'success': False,
                'message': 'No gas-lift capable wells found',
                'allocation': {},
                'total_lift_used': 0.0,
                'total_oil_rate': 0.0,
                'baseline_rate': 0.0,
                'improvement_percent': 0.0,
                'marginal_efficiency_history': [],
            }

        # Step 1: Baseline solve (no lift)
        nodes_baseline = deepcopy(self.nodes)
        for n in nodes_baseline:
            if n.get('kind') == 'well':
                params = n.setdefault('params', {})
                params['gas_lift_injection_sm3d'] = 0.0

        baseline_rate, success_base = self._solve_and_get_rate(nodes_baseline)
        if not success_base or baseline_rate is None:
            baseline_rate = 0.0

        if verbose:
            print(f"[GLA] Baseline rate (no lift): {baseline_rate:.1f} m³/d")

        # Step 2: Iterative allocation
        allocation = {w['id']: 0.0 for w in self.wells}
        nodes_current = deepcopy(self.nodes)
        self.allocation_history = []
        marginal_history = []

        for iteration in range(self.max_iterations):
            lift_remaining = self.total_lift_m3d - sum(allocation.values())
            if lift_remaining < self.min_lift_step_m3d:
                if verbose:
                    print(f"[GLA] Iteration {iteration}: Lift capacity exhausted")
                break

            # Measure marginal for each well
            marginal_map = {}
            for well in self.wells:
                well_id = well['id']
                marginal = self.measure_marginal_efficiency(
                    well_id,
                    allocation[well_id],
                    nodes_current
                )
                marginal_map[well_id] = marginal if marginal is not None else 0.0

            marginal_history.append((sum(allocation.values()), marginal_map.copy()))

            # Find best well
            best_well_id = max(marginal_map.keys(), key=lambda wid: marginal_map[wid])
            best_marginal = marginal_map[best_well_id]

            if best_marginal < 1e-6:
                if verbose:
                    print(f"[GLA] Iteration {iteration}: All marginals < 1e-6, stopping")
                break

            # Allocate step to best well
            allocation[best_well_id] += self.min_lift_step_m3d

            # Update nodes for next iteration
            nodes_current = deepcopy(self.nodes)
            for n in nodes_current:
                if n.get('kind') == 'well' and n['id'] in allocation:
                    params = n.setdefault('params', {})
                    params['gas_lift_injection_sm3d'] = allocation[n['id']]

            if verbose:
                print(f"[GLA] Iter {iteration}: Allocated to {best_well_id} "
                      f"({best_marginal:.3f} m³/d per Sm³/d), "
                      f"Total: {sum(allocation.values()):.0f} Sm³/d")

            self.allocation_history.append(allocation.copy())

        # Step 3: Final solve with optimized allocation
        nodes_final = deepcopy(self.nodes)
        for n in nodes_final:
            if n.get('kind') == 'well' and n['id'] in allocation:
                params = n.setdefault('params', {})
                params['gas_lift_injection_sm3d'] = allocation[n['id']]

        final_rate, success_final = self._solve_and_get_rate(nodes_final)
        if not success_final or final_rate is None:
            final_rate = baseline_rate

        total_lift_used = sum(allocation.values())
        improvement = final_rate - baseline_rate if baseline_rate > 0 else 0.0
        improvement_pct = (improvement / baseline_rate * 100.0) if baseline_rate > 0 else 0.0

        self.marginal_efficiency_curve = marginal_history

        return {
            'success': True,
            'message': f'Optimized allocation across {len(self.wells)} wells',
            'allocation': allocation,
            'total_lift_used': total_lift_used,
            'total_oil_rate': final_rate,
            'baseline_rate': baseline_rate,
            'improvement_percent': improvement_pct,
            'marginal_efficiency_history': marginal_history,
        }

    def allocation_table(self, result: Dict) -> List[Dict]:
        """Format allocation result as table rows.

        Returns:
            List of dicts: [{'Well': id, 'Allocated Lift (Sm³/d)': ..., 'Marginal (m³/d per Sm³/d)': ...}]
        """
        allocation = result.get('allocation', {})

        # Get final marginal values (from last marginal measurement)
        final_marginals = {}
        if result.get('marginal_efficiency_history'):
            _, final_marginals = result['marginal_efficiency_history'][-1]

        rows = []
        for well in self.wells:
            well_id = well['id']
            well_name = well.get('name', well_id)
            lift = allocation.get(well_id, 0.0)
            marginal = final_marginals.get(well_id, 0.0)

            rows.append({
                'Well': well_name,
                'ID': well_id,
                'Allocated Lift (Sm³/d)': f"{lift:.0f}",
                'Marginal Efficiency (m³/d per Sm³/d)': f"{marginal:.4f}",
            })

        return sorted(rows, key=lambda r: float(r['Allocated Lift (Sm³/d)']), reverse=True)

    def marginal_curve_data(self, result: Dict) -> Tuple[List[float], List[float]]:
        """Extract marginal efficiency curve for plotting.

        Returns:
            (cumulative_lift_list, marginal_value_list) for best well at each step
        """
        history = result.get('marginal_efficiency_history', [])

        cumulative_lift = []
        marginals = []

        for total_allocated, marginal_map in history:
            if marginal_map:
                best_marginal = max(marginal_map.values())
                cumulative_lift.append(total_allocated)
                marginals.append(best_marginal)

        return cumulative_lift, marginals


def allocate_lift(
    nodes: List[Dict],
    edges: List[Dict],
    total_lift_m3d: float = 500.0,
    solve_func=None,
    verbose: bool = False,
) -> Dict:
    """Convenience function to run gas-lift allocation.

    Args:
        nodes: Network node list
        edges: Network edge list
        total_lift_m3d: Available lift [Sm³/d]
        solve_func: Network solver (default: imported)
        verbose: Print progress

    Returns:
        Allocation result dict
    """
    allocator = GasLiftAllocator(
        nodes,
        edges,
        total_lift_m3d=total_lift_m3d,
        solve_func=solve_func,
    )
    return allocator.allocate(verbose=verbose)
