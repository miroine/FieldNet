"""Shut-In Optimizer for Marginal Well Analysis (v31).

Determines optimal shut-in decisions for producers to maximize oil production
subject to economic and operational constraints (minimum rate, minimum BHP).

Uses enumeration on binary shut-in state (feasible for 3–10 wells).

Canonical units: bar, m³/d (liquid), days.
"""

from copy import deepcopy
from typing import Dict, List, Optional, Tuple, Set


class ShutdownOptimizer:
    """Optimize shut-in decisions for marginal producers.

    Attributes:
        nodes: Graph node list
        edges: Graph edge list
        wells: List of producer wells to optimize
        min_rate_m3d: Minimum sustained liquid rate [m³/d]
        min_bhp_bar: Minimum flowing bottomhole pressure [bar]
        solve_func: Network solver callable
    """

    def __init__(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        wells: Optional[List[Dict]] = None,
        min_rate_m3d: float = 5.0,
        min_bhp_bar: float = 10.0,
        solve_func=None,
    ):
        """Initialize shut-in optimizer.

        Args:
            nodes: Network node list
            edges: Network edge list
            wells: List of well nodes to optimize (default: all producers)
            min_rate_m3d: Minimum rate to sustain flowing well [m³/d]
            min_bhp_bar: Minimum BHP for flowing well [bar]
            solve_func: Network solver (default: imported from solver.steady_state)
        """
        self.nodes = nodes
        self.edges = edges
        self.min_rate_m3d = max(min_rate_m3d, 0.0)
        self.min_bhp_bar = max(min_bhp_bar, 0.0)

        # Filter wells to optimize
        if wells is None:
            self.wells = [
                n for n in nodes
                if n.get('kind') == 'well' and (n.get('params') or {}).get('available', True)
            ]
        else:
            self.wells = wells

        # Import solver if not provided
        if solve_func is None:
            try:
                from solver.steady_state import solve_network
                self.solve_func = solve_network
            except ImportError:
                self.solve_func = self._mock_solve
        else:
            self.solve_func = solve_func

        self.scenarios_evaluated = 0
        self.feasible_scenarios = []

    def _mock_solve(self, nodes, edges):
        """Mock solver for testing."""
        details = {}
        for n in nodes:
            if n.get('kind') == 'well':
                is_shut = n.get('params', {}).get('is_shut_in', False)
                if is_shut:
                    rate = 0.0
                    bhp = n.get('params', {}).get('reservoir_pressure_bar', 200)
                else:
                    rate = n.get('params', {}).get('pi_m3d_bar', 10.0) * 10
                    bhp = max(n.get('params', {}).get('reservoir_pressure_bar', 200) - rate / 20, 0)

                details[n['id']] = {
                    'liquid_rate_m3d': rate,
                    'bhp_bar': bhp,
                    'is_shut_in': is_shut,
                }
        return (None, None, {'residual': 0}, details)

    def _total_oil_rate(self, details: Dict) -> float:
        """Sum oil rate across flowing wells."""
        return sum(
            v.get('liquid_rate_m3d', 0.0)
            for v in details.values()
            if not v.get('is_shut_in', False)
        )

    def _check_constraints(
        self,
        details: Dict,
        shut_in_mask: int,
    ) -> Tuple[bool, List[str]]:
        """Check if scenario satisfies all constraints.

        Args:
            details: Well details from solve
            shut_in_mask: Bitmask of shut-in state

        Returns:
            (is_valid, violations) tuple
        """
        violations = []

        for i, well in enumerate(self.wells):
            well_id = well['id']
            is_shut = bool(shut_in_mask & (1 << i))
            well_data = details.get(well_id, {})

            if is_shut:
                # Shut-in well: no constraints
                continue

            # Flowing well must meet minimum rate
            rate = well_data.get('liquid_rate_m3d', 0.0)
            if rate < self.min_rate_m3d:
                violations.append(
                    f"{well_id}: rate {rate:.1f} m³/d < min {self.min_rate_m3d} m³/d"
                )

            # Flowing well must meet minimum BHP
            bhp = well_data.get('bhp_bar', 0.0)
            if bhp < self.min_bhp_bar:
                violations.append(
                    f"{well_id}: BHP {bhp:.1f} bar < min {self.min_bhp_bar} bar"
                )

        return (len(violations) == 0, violations)

    def _solve_scenario(
        self,
        shut_in_mask: int,
    ) -> Tuple[Optional[Dict], bool, List[str]]:
        """Solve network for a specific shut-in scenario.

        Args:
            shut_in_mask: Bitmask indicating which wells are shut in

        Returns:
            (details, valid, violations) tuple
        """
        self.scenarios_evaluated += 1

        # Set shut-in state on all wells
        nodes_scenario = deepcopy(self.nodes)
        for i, well in enumerate(self.wells):
            is_shut = bool(shut_in_mask & (1 << i))
            for node in nodes_scenario:
                if node.get('id') == well['id']:
                    params = node.setdefault('params', {})
                    params['is_shut_in'] = is_shut
                    break

        # Solve
        try:
            result = self.solve_func(nodes_scenario, deepcopy(self.edges))
            if result is None:
                return None, False, ["Solver failed"]

            p, q, info, details = result

            # Check constraints
            valid, violations = self._check_constraints(details, shut_in_mask)
            return details, valid, violations
        except Exception as e:
            return None, False, [f"Solver exception: {str(e)}"]

    def optimize_shutdowns(self, verbose: bool = False) -> Dict:
        """Find optimal shut-in configuration.

        Uses enumeration over 2^n states (feasible for n ≤ 10).

        Args:
            verbose: Print evaluation progress

        Returns:
            Dict with keys:
            - 'success': bool
            - 'shut_in_wells': List of shut-in well IDs
            - 'flowing_wells': List of flowing well IDs
            - 'total_oil_rate': float [m³/d]
            - 'constraints_violated': List of violated constraints
            - 'scenarios_evaluated': int
            - 'feasible_scenarios': int
            - 'reason': str (explanation of shut-in decision)
        """
        if not self.wells:
            return {
                'success': False,
                'message': 'No wells to optimize',
                'shut_in_wells': [],
                'flowing_wells': [],
                'total_oil_rate': 0.0,
                'scenarios_evaluated': 0,
                'feasible_scenarios': 0,
            }

        n_wells = len(self.wells)
        if n_wells > 10:
            return {
                'success': False,
                'message': f'Too many wells ({n_wells} > 10). Use MIP solver instead.',
                'shut_in_wells': [],
                'flowing_wells': [],
                'total_oil_rate': 0.0,
                'scenarios_evaluated': 0,
                'feasible_scenarios': 0,
            }

        if verbose:
            print(f"[SO] Enumerating {2**n_wells} scenarios for {n_wells} wells...")

        # Enumerate all 2^n states
        self.scenarios_evaluated = 0
        self.feasible_scenarios = []

        best_rate = -1.0  # best FEASIBLE rate only
        best_mask = (1 << n_wells) - 1  # All wells shut in (fallback)
        best_details = None
        best_violations = []
        # Fallback bookkeeping for the infeasible case is kept separate so an
        # infeasible scenario's (constraint-violating) rate can never block a
        # lower-rate feasible one from being selected.
        fallback_set = False
        fallback_mask = best_mask
        fallback_details = None
        fallback_violations = []

        for mask in range(2 ** n_wells):
            details, valid, violations = self._solve_scenario(mask)

            if details is None:
                continue

            if valid:
                # Feasible scenario
                rate = self._total_oil_rate(details)
                self.feasible_scenarios.append((mask, rate, details))

                if rate > best_rate:
                    best_rate = rate
                    best_mask = mask
                    best_details = details
                    best_violations = []
            elif not fallback_set:
                # Remember first infeasible scenario (for fallback messaging)
                fallback_set = True
                fallback_mask = mask
                fallback_details = details
                fallback_violations = violations

            if verbose and (mask + 1) % max(1, 2 ** n_wells // 8) == 0:
                print(f"  ...{mask + 1} / {2**n_wells} scenarios evaluated")

        if verbose:
            print(f"[SO] {len(self.feasible_scenarios)} feasible scenarios found")

        if not self.feasible_scenarios and fallback_set:
            best_rate = self._total_oil_rate(fallback_details)
            best_mask = fallback_mask
            best_details = fallback_details
            best_violations = fallback_violations

        # Extract optimal state
        shut_in_wells = []
        flowing_wells = []
        for i, well in enumerate(self.wells):
            if best_mask & (1 << i):
                shut_in_wells.append(well['id'])
            else:
                flowing_wells.append(well['id'])

        # Determine reason (only show infeasible if NO feasible scenarios found)
        if len(self.feasible_scenarios) == 0 and best_violations:
            reason = 'Infeasible: ' + '; '.join(best_violations[:2])
        elif len(shut_in_wells) > 0:
            reason = f'Economic marginal analysis: {len(shut_in_wells)} well(s) below min rate/BHP'
        else:
            reason = 'All wells meet constraints'

        return {
            'success': (len(self.feasible_scenarios) > 0 or best_rate >= 0),
            'message': f'Evaluated {self.scenarios_evaluated} scenarios, {len(self.feasible_scenarios)} feasible',
            'shut_in_wells': shut_in_wells,
            'flowing_wells': flowing_wells,
            'total_oil_rate': max(best_rate, 0.0),
            'scenarios_evaluated': self.scenarios_evaluated,
            'feasible_scenarios': len(self.feasible_scenarios),
            'constraints_violated': best_violations,
            'reason': reason,
        }

    def shutdown_table(self, result: Dict) -> List[Dict]:
        """Format shut-in result as table rows.

        Returns:
            List of dicts: [{'Well': id, 'Status': 'Flowing'|'Shut In', 'Reason': ...}]
        """
        shut_in_set = set(result.get('shut_in_wells', []))

        rows = []
        for well in self.wells:
            well_id = well['id']
            well_name = well.get('name', well_id)
            is_shut = well_id in shut_in_set

            status = 'Shut In' if is_shut else 'Flowing'
            reason = (
                'Below min rate' if is_shut
                else 'Meets constraints'
            )

            rows.append({
                'Well': well_name,
                'ID': well_id,
                'Status': status,
                'Reason': reason,
            })

        return sorted(
            rows,
            key=lambda r: (r['Status'] == 'Flowing', r['Well']),
            reverse=True
        )

    def comparison_metrics(
        self,
        result: Dict,
        baseline_rate_m3d: Optional[float] = None,
    ) -> Dict:
        """Compute metrics comparing optimal to baseline.

        Args:
            result: Optimization result
            baseline_rate_m3d: Baseline oil rate (default: all wells flowing)

        Returns:
            Dict with comparison metrics
        """
        optimal_rate = result['total_oil_rate']

        if baseline_rate_m3d is None:
            # Assume baseline is all wells flowing
            baseline_rate_m3d = sum(
                w.get('params', {}).get('pi_m3d_bar', 10.0) * 10
                for w in self.wells
            )

        delta = optimal_rate - baseline_rate_m3d
        pct_change = (delta / baseline_rate_m3d * 100) if baseline_rate_m3d > 0 else 0.0

        return {
            'baseline_rate_m3d': baseline_rate_m3d,
            'optimal_rate_m3d': optimal_rate,
            'delta_rate_m3d': delta,
            'percent_change': pct_change,
            'wells_shut_in': len(result['shut_in_wells']),
            'wells_flowing': len(result['flowing_wells']),
        }


def optimize_shutdowns(
    nodes: List[Dict],
    edges: List[Dict],
    wells: Optional[List[Dict]] = None,
    min_rate_m3d: float = 5.0,
    min_bhp_bar: float = 10.0,
    solve_func=None,
    verbose: bool = False,
) -> Dict:
    """Convenience function to run shut-in optimization.

    Args:
        nodes: Network nodes
        edges: Network edges
        wells: Wells to optimize (default: all producers)
        min_rate_m3d: Minimum sustainable rate
        min_bhp_bar: Minimum flowing BHP
        solve_func: Network solver
        verbose: Print progress

    Returns:
        Optimization result dict
    """
    optimizer = ShutdownOptimizer(
        nodes,
        edges,
        wells=wells,
        min_rate_m3d=min_rate_m3d,
        min_bhp_bar=min_bhp_bar,
        solve_func=solve_func,
    )
    return optimizer.optimize_shutdowns(verbose=verbose)
