"""Unit tests for Gas-Lift Allocation Optimizer (v31).

Tests marginal efficiency measurement, iterative allocation, and integration
with network solver.
"""

import pytest
from copy import deepcopy
from optimization.gas_lift_allocator import GasLiftAllocator, allocate_lift


class MockNetworkSolver:
    """Mock solver for testing gas-lift allocator without full network."""

    def __init__(self, well_responses: dict = None):
        """Initialize mock solver.

        Args:
            well_responses: Dict mapping well_id -> (base_rate, lift_sensitivity)
                           e.g., {'W1': (100, 0.5)} means base rate 100 m³/d,
                           sensitivity 0.5 m³/d per Sm³/d gas-lift
        """
        self.well_responses = well_responses or {}
        self.call_count = 0

    def __call__(self, nodes, edges):
        """Simulate network solve with gas-lift sensitivity."""
        self.call_count += 1

        details = {}
        for node in nodes:
            if node.get('kind') != 'well':
                continue

            well_id = node['id']
            params = node.get('params', {})

            # Get base rate and sensitivity from mock data
            base_rate, sensitivity = self.well_responses.get(
                well_id, (50.0, 0.3)  # defaults
            )

            # Gas-lift effect: rate increases with injection, subject to diminishing returns
            lift = params.get('gas_lift_injection_sm3d', 0.0)
            # Model diminishing returns: rate boost tapers as well approaches max
            rate_boost = sensitivity * lift * (1 - min(lift / 500, 0.7))
            liquid_rate = base_rate + rate_boost

            details[well_id] = {
                'liquid_rate_m3d': max(liquid_rate, 0.0),
                'gas_lift_sm3d': lift,
            }

        # Mock return format: (pressures, flows, info, details)
        info = {'residual': 0.0, 'constraints': []}
        return (None, None, info, details)


class TestGasLiftAllocatorBasics:
    """Test basic gas-lift allocator functionality."""

    @pytest.fixture
    def three_well_network(self):
        """Create a 3-well test network."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'name': 'Well 1',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                    'pi_m3d_bar': 20.0,
                },
            },
            {
                'id': 'W2',
                'kind': 'well',
                'name': 'Well 2',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                    'pi_m3d_bar': 15.0,
                },
            },
            {
                'id': 'W3',
                'kind': 'well',
                'name': 'Well 3',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                    'pi_m3d_bar': 12.0,
                },
            },
        ]
        edges = []
        return nodes, edges

    def test_allocator_creation(self, three_well_network):
        """Test allocator initializes with wells."""
        nodes, edges = three_well_network
        mock_solver = MockNetworkSolver()

        allocator = GasLiftAllocator(
            nodes,
            edges,
            total_lift_m3d=500.0,
            solve_func=mock_solver,
        )

        assert len(allocator.wells) == 3
        assert allocator.total_lift_m3d == 500.0
        assert allocator.wells[0]['id'] == 'W1'

    def test_no_gas_lift_wells(self):
        """Test allocator handles network with no gas-lift wells."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {'lift_type': 'none'},
            },
        ]
        edges = []
        mock_solver = MockNetworkSolver()

        allocator = GasLiftAllocator(
            nodes,
            edges,
            solve_func=mock_solver,
        )

        assert len(allocator.wells) == 0
        result = allocator.allocate()
        assert not result['success']
        assert 'No gas-lift' in result['message']

    def test_allocation_with_mock_solver(self, three_well_network):
        """Test allocation with mock solver."""
        nodes, edges = three_well_network

        # Well 1 has high sensitivity (0.5), wells 2 and 3 moderate (0.3)
        well_responses = {
            'W1': (100.0, 0.5),   # Base 100 m³/d, sensitivity 0.5
            'W2': (80.0, 0.3),    # Base 80 m³/d, sensitivity 0.3
            'W3': (60.0, 0.25),   # Base 60 m³/d, sensitivity 0.25
        }
        mock_solver = MockNetworkSolver(well_responses)

        allocator = GasLiftAllocator(
            nodes,
            edges,
            total_lift_m3d=300.0,
            min_lift_step_m3d=10.0,
            solve_func=mock_solver,
        )

        result = allocator.allocate(verbose=False)

        assert result['success']
        assert sum(result['allocation'].values()) > 0
        assert 'W1' in result['allocation']  # Highest sensitivity should get most lift


class TestMarginalEfficiency:
    """Test marginal efficiency measurement."""

    @pytest.fixture
    def simple_two_well_network(self):
        """Create a simple 2-well network."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'name': 'Well 1',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
            {
                'id': 'W2',
                'kind': 'well',
                'name': 'Well 2',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
        ]
        return nodes, []

    def test_marginal_measurement(self, simple_two_well_network):
        """Test marginal efficiency measurement."""
        nodes, edges = simple_two_well_network

        # Well 1: high sensitivity (1.0 m³/d per Sm³/d lift)
        well_responses = {
            'W1': (100.0, 1.0),
            'W2': (80.0, 0.3),
        }
        mock_solver = MockNetworkSolver(well_responses)

        allocator = GasLiftAllocator(
            nodes,
            edges,
            min_lift_step_m3d=10.0,
            solve_func=mock_solver,
        )

        # Measure marginal for W1 at 0 lift
        marginal = allocator.measure_marginal_efficiency('W1', 0.0, nodes)
        assert marginal is not None
        assert marginal > 0
        # For simplicity, check it's in reasonable range
        assert 0.5 < marginal < 2.0


class TestAllocationIterations:
    """Test iterative allocation behavior."""

    def test_allocation_convergence(self):
        """Test allocation converges and respects capacity."""
        nodes = [
            {
                'id': f'W{i}',
                'kind': 'well',
                'name': f'Well {i}',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            }
            for i in range(1, 4)
        ]
        edges = []

        # Wells have different sensitivities; allocator should favor high-sensitivity wells
        well_responses = {
            'W1': (100.0, 0.8),   # High sensitivity
            'W2': (80.0, 0.4),    # Medium
            'W3': (60.0, 0.15),   # Low
        }
        mock_solver = MockNetworkSolver(well_responses)

        allocator = GasLiftAllocator(
            nodes,
            edges,
            total_lift_m3d=100.0,
            min_lift_step_m3d=10.0,
            max_iterations=15,
            solve_func=mock_solver,
        )

        result = allocator.allocate(verbose=False)

        assert result['success']
        total_allocated = sum(result['allocation'].values())
        assert total_allocated <= 100.1  # Allow small numerical error

        # Well 1 should get most lift (highest sensitivity)
        assert result['allocation']['W1'] >= result['allocation']['W2']
        assert result['allocation']['W2'] >= result['allocation']['W3']


class TestAllocationTable:
    """Test allocation table formatting."""

    def test_allocation_table_format(self):
        """Test table output format."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'name': 'Test Well 1',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
            {
                'id': 'W2',
                'kind': 'well',
                'name': 'Test Well 2',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
        ]
        edges = []

        well_responses = {
            'W1': (100.0, 0.6),
            'W2': (80.0, 0.3),
        }
        mock_solver = MockNetworkSolver(well_responses)

        allocator = GasLiftAllocator(
            nodes,
            edges,
            total_lift_m3d=100.0,
            solve_func=mock_solver,
        )

        result = allocator.allocate()
        table = allocator.allocation_table(result)

        assert len(table) == 2
        assert all('Well' in row for row in table)
        assert all('Allocated Lift (Sm³/d)' in row for row in table)
        assert all('Marginal Efficiency (m³/d per Sm³/d)' in row for row in table)

        # Check table is sorted by lift (descending)
        lifts = [float(row['Allocated Lift (Sm³/d)']) for row in table]
        assert lifts == sorted(lifts, reverse=True)


class TestMarginalCurve:
    """Test marginal efficiency curve generation."""

    def test_marginal_curve_diminishing_returns(self):
        """Test marginal curve shows diminishing returns."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
        ]
        edges = []

        # Single well with diminishing returns built into mock solver
        well_responses = {'W1': (100.0, 0.5)}
        mock_solver = MockNetworkSolver(well_responses)

        allocator = GasLiftAllocator(
            nodes,
            edges,
            total_lift_m3d=200.0,
            min_lift_step_m3d=20.0,
            solve_func=mock_solver,
        )

        result = allocator.allocate()
        cumulative_lift, marginals = allocator.marginal_curve_data(result)

        assert len(cumulative_lift) > 0
        assert len(marginals) == len(cumulative_lift)

        # Check that marginals show diminishing returns (generally decreasing)
        # Note: Due to mock's simple model, this is approximate
        if len(marginals) > 1:
            # Allow some noise, but overall trend should be decreasing
            decrease_count = sum(1 for i in range(1, len(marginals)) if marginals[i] < marginals[i-1])
            assert decrease_count >= len(marginals) - 2  # At least 50% decreasing


class TestConvenienceFunction:
    """Test module-level convenience function."""

    def test_allocate_lift_convenience(self):
        """Test allocate_lift() convenience function."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
        ]
        edges = []

        mock_solver = MockNetworkSolver({'W1': (100.0, 0.5)})

        result = allocate_lift(
            nodes,
            edges,
            total_lift_m3d=150.0,
            solve_func=mock_solver,
            verbose=False,
        )

        assert result['success']
        assert 'allocation' in result
        assert 'total_oil_rate' in result


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_zero_lift_available(self):
        """Test allocator with zero lift available."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {
                    'lift_type': 'gas_lift',
                    'gas_lift_injection_sm3d': 0.0,
                    'available': True,
                },
            },
        ]
        edges = []
        mock_solver = MockNetworkSolver({'W1': (100.0, 0.5)})

        allocator = GasLiftAllocator(
            nodes,
            edges,
            total_lift_m3d=0.0,  # No lift
            solve_func=mock_solver,
        )

        result = allocator.allocate()
        assert result['success']
        assert result['total_lift_used'] == 0.0

    def test_unavailable_wells_ignored(self):
        """Test that unavailable wells are filtered out."""
        nodes = [
            {
                'id': 'W1',
                'kind': 'well',
                'params': {
                    'lift_type': 'gas_lift',
                    'available': True,
                },
            },
            {
                'id': 'W2',
                'kind': 'well',
                'params': {
                    'lift_type': 'gas_lift',
                    'available': False,  # Unavailable
                },
            },
        ]
        edges = []
        mock_solver = MockNetworkSolver()

        allocator = GasLiftAllocator(
            nodes,
            edges,
            solve_func=mock_solver,
        )

        # Only W1 should be included
        assert len(allocator.wells) == 1
        assert allocator.wells[0]['id'] == 'W1'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
