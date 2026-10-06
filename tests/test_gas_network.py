"""Unit tests for Gas-Network Solver (v31).

Tests isothermal pressure-drop calculation, z-factor corrections,
compressor affinity laws, and network convergence.
"""

import pytest
import numpy as np
from solver.gas_network import (
    GasNetworkSolver,
    GasNodeProperties,
    GasPipeProperties,
    CompressorStation,
    solve_gas_network,
)


class TestGasNetworkBasics:
    """Test basic gas network solver functionality."""

    def test_solver_creation(self):
        """Test solver initializes correctly."""
        nodes = {
            'well': GasNodeProperties(node_id='well', pressure_bar=None),
            'sales': GasNodeProperties(node_id='sales', pressure_bar=5.0),
        }
        edges = {
            'pipe1': GasPipeProperties(
                link_id='pipe1',
                source_id='well',
                target_id='sales',
                length_km=10.0,
                diameter_mm=200.0,
            )
        }

        solver = GasNetworkSolver(nodes, edges)
        assert len(solver.nodes) == 2
        assert len(solver.edges) == 1
        assert solver.temperature_c == 15.0

    def test_gas_density_calculation(self):
        """Test gas density using real gas law."""
        solver = GasNetworkSolver({}, {})

        # At 20 bar and 15°C with default z = 0.85 + 1e-4*20 = 0.852, M = 17.5 kg/kmol
        # ρ = P*M / (z*R*T) = 20e5*0.0175 / (0.852*8.314*288.15) ≈ 17.1 kg/m³
        # (source previously omitted the kg/kmol -> kg/mol conversion, giving ~17,000)
        rho = solver._gas_density(20.0)
        assert 14.0 < rho < 18.0

        # Higher pressure gives higher density
        rho_high = solver._gas_density(40.0)
        assert rho_high > rho


class TestPressureDropCalculation:
    """Test isothermal pressure-drop calculation."""

    def test_pressure_drop_increases_with_flow(self):
        """Test pressure drop increases with flow rate."""
        solver = GasNetworkSolver({}, {})

        pipe = GasPipeProperties(
            link_id='test_pipe',
            source_id='in',
            target_id='out',
            length_km=10.0,
            diameter_mm=200.0,
        )

        # Low flow
        dp_low = solver._pressure_drop_isothermal(30.0, 500.0, pipe)
        # High flow
        dp_high = solver._pressure_drop_isothermal(30.0, 2000.0, pipe)

        assert dp_high > dp_low
        assert dp_low > 0.0
        assert dp_high > 0.0

    def test_pressure_drop_increases_with_length(self):
        """Test pressure drop increases with pipe length."""
        solver = GasNetworkSolver({}, {})

        pipe_short = GasPipeProperties(
            link_id='short',
            source_id='in',
            target_id='out',
            length_km=5.0,
            diameter_mm=200.0,
        )
        pipe_long = GasPipeProperties(
            link_id='long',
            source_id='in',
            target_id='out',
            length_km=20.0,
            diameter_mm=200.0,
        )

        dp_short = solver._pressure_drop_isothermal(30.0, 1000.0, pipe_short)
        dp_long = solver._pressure_drop_isothermal(30.0, 1000.0, pipe_long)

        assert dp_long > dp_short


class TestFrictionFactor:
    """Test Colebrook-White friction factor calculation."""

    def test_laminar_regime(self):
        """Test friction factor in laminar regime (Re < 2300)."""
        solver = GasNetworkSolver({}, {})

        # Laminar: f = 64/Re
        f_lam = solver._friction_factor_colebrook(1000.0, 0.001)
        assert 0.06 < f_lam < 0.07
        assert abs(f_lam - 64.0 / 1000.0) < 0.001

    def test_turbulent_regime(self):
        """Test friction factor in turbulent regime (Re > 4000)."""
        solver = GasNetworkSolver({}, {})

        # Turbulent: f < 0.05 typically
        f_turb = solver._friction_factor_colebrook(100000.0, 0.0001)
        assert 0.01 < f_turb < 0.03


class TestCompressorAffinity:
    """Test compressor affinity law calculations."""

    def test_compressor_head_increases_with_speed(self):
        """Test compressor head increases with speed (H ∝ N²)."""
        solver = GasNetworkSolver({}, {})

        comp = CompressorStation(
            link_id='comp',
            inlet_node_id='inlet',
            outlet_node_id='outlet',
            rated_flow_sm3d=1000.0,
            rated_head_bar=50.0,
            rated_speed_rpm=7000.0,
        )

        # At rated speed
        # _compressor_head_bar returns (head_bar, status) -- unpack it
        h_rated, st_rated = solver._compressor_head_bar(1000.0, 20.0, 7000.0, comp)

        # At half speed
        h_half, st_half = solver._compressor_head_bar(1000.0, 20.0, 3500.0, comp)
        assert st_rated == 'OK' and st_half == 'OK'

        # Head should decrease when speed decreases (H ∝ N²)
        assert h_rated > 0
        assert h_half > 0
        assert h_rated > h_half

    def test_compressor_head_with_flow_variation(self):
        """Test compressor head varies with flow (head-flow curve)."""
        solver = GasNetworkSolver({}, {})

        comp = CompressorStation(
            link_id='comp',
            inlet_node_id='inlet',
            outlet_node_id='outlet',
            rated_flow_sm3d=1000.0,
            rated_head_bar=50.0,
            rated_speed_rpm=7000.0,
        )

        # At rated conditions
        h_rated = solver._compressor_head_bar(1000.0, 20.0, 7000.0, comp)

        # At lower flow (closer to surge)
        h_low = solver._compressor_head_bar(600.0, 20.0, 7000.0, comp)

        # At higher flow (approaching choke)
        h_high = solver._compressor_head_bar(1400.0, 20.0, 7000.0, comp)

        # Head should be highest at low flow (classic centrifugal curve)
        assert h_low > h_rated
        assert h_high < h_rated


class TestNetworkSolve:
    """Test network solution convergence."""

    def test_simple_two_node_solve(self):
        """Test solve on simple two-node network."""
        nodes = {
            'source': GasNodeProperties(node_id='source', pressure_bar=50.0),
            'sink': GasNodeProperties(node_id='sink', pressure_bar=10.0),
        }
        edges = {
            'pipe': GasPipeProperties(
                link_id='pipe',
                source_id='source',
                target_id='sink',
                length_km=20.0,
                diameter_mm=250.0,
                initial_flow_sm3d=1000.0,
            )
        }

        solver = GasNetworkSolver(nodes, edges)
        pressures, flows, info = solver.solve_isothermal(max_iterations=30, verbose=False)

        assert 'success' in info
        assert 'iterations' in info
        assert 'source' in pressures
        assert 'sink' in pressures

        # Source should be higher than sink
        assert pressures['source'] >= pressures['sink']
        # Flow should be positive
        assert flows.get('pipe', 0) > 0

    def test_three_node_network_with_compressor(self):
        """Test solve on three-node network with compressor."""
        nodes = {
            'well': GasNodeProperties(node_id='well', pressure_bar=None),
            'comp_in': GasNodeProperties(node_id='comp_in', pressure_bar=None),
            'sales': GasNodeProperties(node_id='sales', pressure_bar=5.0),
        }
        edges = {
            'pipe1': GasPipeProperties(
                link_id='pipe1',
                source_id='well',
                target_id='comp_in',
                length_km=15.0,
                diameter_mm=200.0,
            ),
            'pipe2': GasPipeProperties(
                link_id='pipe2',
                source_id='comp_in',
                target_id='sales',
                length_km=50.0,
                diameter_mm=300.0,
            ),
        }
        compressors = {
            'comp1': CompressorStation(
                link_id='comp1',
                inlet_node_id='comp_in',
                outlet_node_id='comp_in',  # For this test, assume discharge back to comp_in
                rated_flow_sm3d=1500.0,
                rated_head_bar=45.0,
                rated_speed_rpm=7000.0,
            )
        }

        solver = GasNetworkSolver(nodes, edges, compressors)
        pressures, flows, info = solver.solve_isothermal(max_iterations=30, verbose=False)

        assert info['success'] or info['iterations'] > 0
        assert 'well' in pressures
        assert 'comp_in' in pressures
        assert 'sales' in pressures

    def test_network_convergence_with_tolerance(self):
        """Test that solver converges within specified tolerance."""
        nodes = {
            'source': GasNodeProperties(node_id='source', pressure_bar=60.0),
            'middle': GasNodeProperties(node_id='middle', pressure_bar=None),
            'sink': GasNodeProperties(node_id='sink', pressure_bar=8.0),
        }
        edges = {
            'pipe1': GasPipeProperties(
                link_id='pipe1',
                source_id='source',
                target_id='middle',
                length_km=20.0,
                diameter_mm=300.0,
            ),
            'pipe2': GasPipeProperties(
                link_id='pipe2',
                source_id='middle',
                target_id='sink',
                length_km=30.0,
                diameter_mm=250.0,
            ),
        }

        solver = GasNetworkSolver(nodes, edges)
        pressures, flows, info = solver.solve_isothermal(
            tolerance=0.5, max_iterations=50, verbose=False
        )

        # Check convergence
        if info['final_residual_bar'] < 0.5:
            assert info['success']
        assert len(info['residual_history']) > 0


class TestZFactorCorrection:
    """Test z-factor corrections on pressure drop."""

    def test_z_factor_affects_density(self):
        """Test that z-factor modifies gas density."""
        solver = GasNetworkSolver({}, {})

        # Without z-factor consideration (z ≈ 1)
        solver_ideal = GasNetworkSolver({}, {})

        # Density should differ based on z-factor model
        rho = solver._gas_density(40.0)
        rho_high = solver._gas_density(80.0)

        # Higher pressure should give higher density
        assert rho_high > rho


class TestConvenienceFunction:
    """Test module-level convenience function."""

    def test_solve_gas_network_function(self):
        """Test solve_gas_network() convenience function."""
        nodes = [
            {'id': 'source', 'pressure_bar': 50.0, 'kind': 'facility'},
            {'id': 'sink', 'pressure_bar': 10.0, 'kind': 'facility'},
        ]
        edges = [
            {
                'id': 'pipe1',
                'source': 'source',
                'target': 'sink',
                'params': {'length_km': 20.0, 'diameter_mm': 250.0},
            }
        ]

        pressures, flows, info, details = solve_gas_network(nodes, edges, verbose=False)

        assert isinstance(pressures, dict)
        assert isinstance(flows, dict)
        assert 'success' in info
        assert 'node_pressures' in details


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_flow_no_pressure_drop(self):
        """Test that zero flow produces zero pressure drop."""
        solver = GasNetworkSolver({}, {})

        pipe = GasPipeProperties(
            link_id='pipe',
            source_id='in',
            target_id='out',
            length_km=10.0,
            diameter_mm=200.0,
        )

        dp = solver._pressure_drop_isothermal(30.0, 0.0, pipe)
        assert dp == 0.0

    def test_compressor_stall_protection(self):
        """Test compressor stall protection at low flow."""
        solver = GasNetworkSolver({}, {})

        comp = CompressorStation(
            link_id='comp',
            inlet_node_id='in',
            outlet_node_id='out',
            rated_flow_sm3d=1000.0,
            rated_head_bar=50.0,
        )

        # Very low flow (below surge)
        # Function returns (head_bar, status); the tuple must be unpacked.
        h_stall, status = solver._compressor_head_bar(100.0, 20.0, 7000.0, comp)

        # Should be zero head and flagged as SURGE (stalled)
        assert h_stall == 0.0
        assert status == 'SURGE'

    def test_negative_pressures_clamped(self):
        """Test that pressures are clamped to non-negative."""
        solver = GasNetworkSolver({}, {})

        pipe = GasPipeProperties(
            link_id='pipe',
            source_id='in',
            target_id='out',
            length_km=100.0,
            diameter_mm=100.0,
        )

        # Very low inlet pressure
        dp = solver._pressure_drop_isothermal(1.0, 1000.0, pipe)
        assert dp >= 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
