"""Unit tests for Shut-In Optimizer (v31).

Tests binary enumeration, constraint checking, and economic analysis
for shut-in decisions.
"""

import pytest
from copy import deepcopy
from optimization.shutdown_optimizer import ShutdownOptimizer, optimize_shutdowns


class MockShutdownSolver:
    """Mock solver for shut-in optimizer testing."""

    def __init__(self, well_performance: dict = None):
        """Initialize mock solver.

        Args:
            well_performance: Dict mapping well_id -> (base_rate, bhp_when_flowing)
                             e.g., {'W1': (150, 50)} means 150 m³/d at 50 bar BHP
        """
        self.well_performance = well_performance or {}
        self.call_count = 0

    def __call__(self, nodes, edges):
        """Simulate network solve with shut-in state."""
        self.call_count += 1

        details = {}
        for node in nodes:
            if node.get('kind') != 'well':
                continue

            well_id = node['id']
            params = node.get('params', {})
            is_shut = params.get('is_shut_in', False)

            # Get well performance
            base_rate, bhp_flowing = self.well_performance.get(
                well_id, (100.0, 50.0)
            )

            if is_shut:
                # Shut-in well produces nothing
                rate = 0.0
                # BHP rises to reservoir pressure when shut in
                bhp = params.get('reservoir_pressure_bar', 200.0)
            else:
                # Flowing well produces at base rate
                rate = base_rate
                bhp = bhp_flowing

            details[well_id] = {
                'liquid_rate_m3d': rate,
                'bhp_bar': bhp,
                'is_shut_in': is_shut,
            }

        return (None, None, {'residual': 0.0}, details)


class TestShutdownOptimizerBasics:
    """Test basic shut-in optimizer functionality."""

    @pytest.fixture
    def three_well_network(self):
        """Create a 3-well test network."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'name': 'Well 1',
                'params': {
                    'available': True,
                    'reservoir_pressure_bar': 200.0,
                },
            },
            {
                'id': 'W2',
                'kind': 'well',
                'name': 'Well 2',
                'params': {
                    'available': True,
                    'reservoir_pressure_bar': 200.0,
                },
            },
            {
                'id': 'W3',
                'kind': 'well',
                'name': 'Well 3',
                'params': {
                    'available': True,
                    'reservoir_pressure_bar': 200.0,
                },
            },
        ]
        edges = []
        return nodes, edges

    def test_optimizer_creation(self, three_well_network):
        """Test optimizer initializes correctly."""
        nodes, edges = three_well_network
        mock_solver = MockShutdownSolver()

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=5.0,
            min_bhp_bar=10.0,
            solve_func=mock_solver,
        )

        assert len(optimizer.wells) == 3
        assert optimizer.min_rate_m3d == 5.0
        assert optimizer.min_bhp_bar == 10.0

    def test_no_wells_to_optimize(self):
        """Test optimizer handles network with no wells."""
        nodes = [
            {'id': 'node1', 'kind': 'facility', 'params': {}},
        ]
        edges = []
        mock_solver = MockShutdownSolver()

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            solve_func=mock_solver,
        )

        assert len(optimizer.wells) == 0
        result = optimizer.optimize_shutdowns()
        assert not result['success']
        assert 'No wells' in result['message']


class TestEnumeration:
    """Test binary enumeration of shut-in scenarios."""

    # TEST FIX: the test labelled W2 (20 m3/d) as "below minimum" but set
    # min_rate_m3d=10, so W2 actually satisfies the constraint (20 >= 10, BHP
    # floor 0). Since shutting in a feasible producer only loses oil, the
    # optimizer correctly keeps both wells flowing; the old expectation
    # contradicted the stated objective (maximize oil subject to constraints).
    # The minimum is raised to 30 m3/d so W2 is genuinely infeasible while W1
    # (100 m3/d) is not, which is what the scenario intends to exercise.
    def test_enumeration_two_wells(self):
        """Test enumeration with 2 wells (4 scenarios)."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {'available': True},
            },
            {
                'id': 'W2',
                'kind': 'well',
                'params': {'available': True},
            },
        ]
        edges = []

        # W1: good well (100 m³/d), W2: marginal (20 m³/d)
        well_perf = {
            'W1': (100.0, 50.0),
            'W2': (20.0, 15.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=30.0,  # W2 (20 m3/d) below minimum
            min_bhp_bar=0.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns(verbose=False)

        # Should evaluate 4 scenarios (2^2)
        assert result['scenarios_evaluated'] == 4
        # W2 should be shut in (below minimum rate)
        assert 'W2' in result['shut_in_wells']
        assert 'W1' in result['flowing_wells']

    def test_enumeration_three_wells(self):
        """Test enumeration with 3 wells (8 scenarios)."""
        nodes = [
            {
                'id': f'W{i}',
                'kind': 'well',
                'params': {'available': True},
            }
            for i in range(1, 4)
        ]
        edges = []

        well_perf = {
            'W1': (150.0, 60.0),  # Good producer
            'W2': (80.0, 40.0),   # Medium producer
            'W3': (30.0, 20.0),   # Marginal producer
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=25.0,  # W3 just meets minimum
            min_bhp_bar=10.0,   # All meet BHP minimum
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()

        # Should evaluate 8 scenarios
        assert result['scenarios_evaluated'] == 8
        # All wells meet constraints; none should be shut in
        assert len(result['shut_in_wells']) == 0
        assert len(result['flowing_wells']) == 3


class TestConstraintChecking:
    """Test constraint validation (minimum rate and BHP)."""

    def test_minimum_rate_constraint(self):
        """Test minimum rate constraint enforcement."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {'available': True},
            },
            {
                'id': 'W2',
                'kind': 'well',
                'params': {'available': True},
            },
        ]
        edges = []

        # W1: 100 m³/d (above 50 min), W2: 30 m³/d (below 50 min)
        well_perf = {
            'W1': (100.0, 50.0),
            'W2': (30.0, 40.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=50.0,
            min_bhp_bar=0.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()

        # W2 should be shut in (below min rate)
        assert 'W2' in result['shut_in_wells']
        assert 'W1' in result['flowing_wells']
        assert 'rate' in result['reason'].lower()

    def test_minimum_bhp_constraint(self):
        """Test minimum BHP constraint enforcement."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {'available': True},
            },
            {
                'id': 'W2',
                'kind': 'well',
                'params': {'available': True},
            },
        ]
        edges = []

        # W1: high BHP (60 bar), W2: low BHP (5 bar, below 10 min)
        well_perf = {
            'W1': (100.0, 60.0),
            'W2': (80.0, 5.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=5.0,
            min_bhp_bar=10.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()

        # W2 should be shut in (below min BHP)
        assert 'W2' in result['shut_in_wells']
        assert 'BHP' in result['reason']

    def test_both_constraints_together(self):
        """Test multiple constraints applied simultaneously."""
        nodes = [
            {'id': 'W1', 'kind': 'well', 'params': {'available': True}},
            {'id': 'W2', 'kind': 'well', 'params': {'available': True}},
            {'id': 'W3', 'kind': 'well', 'params': {'available': True}},
        ]
        edges = []

        well_perf = {
            'W1': (200.0, 80.0),  # Good: meets both constraints
            'W2': (30.0, 50.0),   # Bad: below min rate
            'W3': (100.0, 5.0),   # Bad: below min BHP
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=50.0,
            min_bhp_bar=10.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()

        # W2 and W3 should be shut in
        assert set(result['shut_in_wells']) == {'W2', 'W3'}
        assert result['flowing_wells'] == ['W1']


class TestShutdownTable:
    """Test shut-in result table formatting."""

    def test_shutdown_table_format(self):
        """Test table output format."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'name': 'Producer 1',
                'params': {'available': True},
            },
            {
                'id': 'W2',
                'kind': 'well',
                'name': 'Producer 2',
                'params': {'available': True},
            },
        ]
        edges = []

        well_perf = {
            'W1': (150.0, 60.0),
            'W2': (20.0, 40.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=50.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()
        table = optimizer.shutdown_table(result)

        assert len(table) == 2
        assert all('Well' in row for row in table)
        assert all('Status' in row for row in table)
        assert all('Reason' in row for row in table)

        # Check that status is either Flowing or Shut In
        statuses = {row['Status'] for row in table}
        assert statuses.issubset({'Flowing', 'Shut In'})


class TestComparisonMetrics:
    """Test comparison metrics generation."""

    def test_metrics_calculation(self):
        """Test metrics computation."""
        nodes = [
            {'id': 'W1', 'kind': 'well', 'params': {'available': True}},
            {'id': 'W2', 'kind': 'well', 'params': {'available': True}},
        ]
        edges = []

        well_perf = {
            'W1': (150.0, 60.0),
            'W2': (50.0, 40.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=60.0,  # W2 just below minimum
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()
        metrics = optimizer.comparison_metrics(result)

        assert 'baseline_rate_m3d' in metrics
        assert 'optimal_rate_m3d' in metrics
        assert 'delta_rate_m3d' in metrics
        assert 'percent_change' in metrics

        # Baseline should be 200 (150 + 50)
        # Optimal should be 150 (only W1, W2 shut in)
        # Delta should be -50
        assert abs(metrics['delta_rate_m3d'] - (-50.0)) < 0.1


class TestConvenienceFunction:
    """Test module-level convenience function."""

    def test_optimize_shutdowns_function(self):
        """Test optimize_shutdowns() convenience function."""
        nodes = [
            {'id': 'W1', 'kind': 'well', 'params': {'available': True}},
            {'id': 'W2', 'kind': 'well', 'params': {'available': True}},
        ]
        edges = []

        well_perf = {
            'W1': (100.0, 50.0),
            'W2': (20.0, 30.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        result = optimize_shutdowns(
            nodes,
            edges,
            min_rate_m3d=30.0,
            min_bhp_bar=0.0,
            solve_func=mock_solver,
            verbose=False,
        )

        assert result['success']
        assert 'shut_in_wells' in result
        assert 'flowing_wells' in result


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_too_many_wells(self):
        """Test optimizer rejects network with > 10 wells (not enumerate-able)."""
        nodes = [
            {'id': f'W{i}', 'kind': 'well', 'params': {'available': True}}
            for i in range(1, 12)  # 11 wells
        ]
        edges = []
        mock_solver = MockShutdownSolver()

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()
        assert not result['success']
        assert 'Too many wells' in result['message']

    def test_all_wells_above_constraints(self):
        """Test scenario where all wells meet constraints."""
        nodes = [
            {'id': 'W1', 'kind': 'well', 'params': {'available': True}},
            {'id': 'W2', 'kind': 'well', 'params': {'available': True}},
        ]
        edges = []

        # Both wells well above minimums
        well_perf = {
            'W1': (300.0, 100.0),
            'W2': (250.0, 90.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=10.0,
            min_bhp_bar=10.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()

        # No wells should be shut in
        assert len(result['shut_in_wells']) == 0
        assert len(result['flowing_wells']) == 2
        assert 'All wells meet' in result['reason']

    def test_all_wells_below_constraints(self):
        """Test scenario where all wells fail constraints."""
        nodes = [
            {'id': 'W1', 'kind': 'well', 'params': {'available': True}},
            {'id': 'W2', 'kind': 'well', 'params': {'available': True}},
        ]
        edges = []

        # Both wells well below minimums
        well_perf = {
            'W1': (5.0, 5.0),      # Below rate and BHP minimums
            'W2': (3.0, 3.0),
        }
        mock_solver = MockShutdownSolver(well_perf)

        optimizer = ShutdownOptimizer(
            nodes,
            edges,
            min_rate_m3d=50.0,
            min_bhp_bar=30.0,
            solve_func=mock_solver,
        )

        result = optimizer.optimize_shutdowns()

        # All wells should be shut in (no feasible flowing configuration)
        assert len(result['shut_in_wells']) == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
