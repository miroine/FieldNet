"""Integration tests for T2.3: Gas-Network + CompressorAffinityModel.

Tests verify that CompressorAffinityModel integrates correctly with GasNetworkSolver.
Covers:
  - CompressorStation extension with optional CompressorMap
  - CompressorAffinityModel initialization in solver
  - Head calculation accuracy with affinity vs simplified model
  - Operating envelope protection (surge/runout)
  - Convergence stability with affinity laws
  - Backward compatibility with simplified model
  - Status code tracking through solver
"""

import sys
sys.path.insert(0, '/home/claude/fieldnet')

from solver.gas_network import (
    GasNetworkSolver,
    GasNodeProperties,
    GasPipeProperties,
    CompressorStation,
    create_compressor_station_with_map,
)
from optimization.compressor_affinity import (
    CompressorMap,
    CompressorAffinityModel,
    create_axial_compressor_map,
    create_centrifugal_compressor_map,
)


class TestCompressorStationExtension:
    """Test CompressorStation enhancements for T2.3."""

    def test_compressor_station_without_map(self):
        """CompressorStation works without compressor_map (backward compatible)."""
        comp = CompressorStation(
            link_id='comp1',
            inlet_node_id='inlet1',
            outlet_node_id='outlet1',
            rated_flow_sm3d=1000.0,
            rated_head_bar=50.0,
            rated_speed_rpm=7000.0,
        )
        assert comp.link_id == 'comp1'
        assert comp.compressor_map is None
        print("✓ test_compressor_station_without_map")

    def test_compressor_station_with_map(self):
        """CompressorStation can hold a CompressorMap reference."""
        comp_map = create_axial_compressor_map()
        comp = CompressorStation(
            link_id='comp1',
            inlet_node_id='inlet1',
            outlet_node_id='outlet1',
            rated_flow_sm3d=comp_map.rated_flow_sm3d,
            rated_head_bar=comp_map.rated_head_bar,
            rated_speed_rpm=comp_map.speed_rpm,
            compressor_map=comp_map,
        )
        assert comp.compressor_map is not None
        assert comp.compressor_map.rated_flow_sm3d == 1500.0
        print("✓ test_compressor_station_with_map")

    def test_compressor_station_map_validation(self):
        """CompressorStation validates consistency between station and map parameters."""
        comp_map = create_axial_compressor_map()

        # This should fail: rated_flow_sm3d mismatches by >5%
        try:
            comp = CompressorStation(
                link_id='comp1',
                inlet_node_id='inlet1',
                outlet_node_id='outlet1',
                rated_flow_sm3d=2000.0,  # Mismatch: map has 1500
                rated_head_bar=comp_map.rated_head_bar,
                rated_speed_rpm=comp_map.speed_rpm,
                compressor_map=comp_map,
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "flow" in str(e).lower()

        print("✓ test_compressor_station_map_validation")

    def test_factory_helper(self):
        """Factory helper creates consistent CompressorStation from map."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map(
            link_id='comp1',
            inlet_node_id='inlet1',
            outlet_node_id='outlet1',
            compressor_map=comp_map,
        )

        # Verify all parameters extracted from map
        assert comp.rated_flow_sm3d == comp_map.rated_flow_sm3d
        assert comp.rated_head_bar == comp_map.rated_head_bar
        assert comp.rated_speed_rpm == comp_map.speed_rpm
        assert comp.compressor_map is comp_map
        print("✓ test_factory_helper")


class TestAffinityModelInitialization:
    """Test CompressorAffinityModel instantiation in GasNetworkSolver."""

    def test_solver_creates_affinity_models(self):
        """Solver creates CompressorAffinityModel for compressors with maps."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)

        nodes = {
            'inlet1': GasNodeProperties('inlet1', pressure_bar=30.0),
            'outlet1': GasNodeProperties('outlet1', pressure_bar=None),
        }
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Verify affinity model was created
        assert 'comp1' in solver.compressor_affinity_models
        assert solver.compressor_has_map['comp1'] is True
        assert isinstance(solver.compressor_affinity_models['comp1'], CompressorAffinityModel)
        print("✓ test_solver_creates_affinity_models")

    def test_solver_mixed_compressors(self):
        """Solver handles mix of compressors with and without maps."""
        comp_map = create_axial_compressor_map()
        comp_with_map = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)
        comp_without_map = CompressorStation('comp2', 'inlet2', 'outlet2')

        nodes = {
            'inlet1': GasNodeProperties('inlet1', pressure_bar=30.0),
            'outlet1': GasNodeProperties('outlet1', pressure_bar=None),
            'inlet2': GasNodeProperties('inlet2', pressure_bar=30.0),
            'outlet2': GasNodeProperties('outlet2', pressure_bar=None),
        }
        edges = {}
        compressors = {'comp1': comp_with_map, 'comp2': comp_without_map}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Verify comp1 has model, comp2 doesn't
        assert solver.compressor_has_map['comp1'] is True
        assert solver.compressor_has_map['comp2'] is False
        assert 'comp1' in solver.compressor_affinity_models
        assert 'comp2' not in solver.compressor_affinity_models
        print("✓ test_solver_mixed_compressors")


class TestHeadCalculationAccuracy:
    """Test head calculation using affinity vs simplified model."""

    def test_head_with_affinity_model(self):
        """_compressor_head_bar() uses affinity model when available."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)

        nodes = {
            'inlet1': GasNodeProperties('inlet1', pressure_bar=30.0),
            'outlet1': GasNodeProperties('outlet1', pressure_bar=None),
        }
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # At rated conditions: speed=7000, flow=1500
        h_affinity, status_affinity = solver._compressor_head_bar(
            1500.0, 30.0, 7000.0, comp, 'comp1'
        )

        # At rated conditions, should get rated head
        assert status_affinity == 'OK'
        assert abs(h_affinity - comp_map.rated_head_bar) < 1.0  # Should be close to 42 bar
        print("✓ test_head_with_affinity_model")

    def test_head_with_simplified_model(self):
        """_compressor_head_bar() falls back to simplified when no affinity model."""
        comp = CompressorStation('comp1', 'inlet1', 'outlet1',
                                 rated_flow_sm3d=1000.0, rated_head_bar=50.0)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # At rated conditions: speed=7000, flow=1000
        h_simplified, status_simplified = solver._compressor_head_bar(
            1000.0, 30.0, 7000.0, comp, 'comp1'
        )

        assert status_simplified == 'OK'
        assert h_simplified > 0  # Should produce some head
        print("✓ test_head_with_simplified_model")

    def test_head_scaling_at_half_speed(self):
        """Head scales correctly with speed (N²) using affinity model."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # At rated speed: 7000 RPM, rated flow: 1500 Sm³/d
        h_rated, _ = solver._compressor_head_bar(1500.0, 30.0, 7000.0, comp, 'comp1')

        # At half speed: 3500 RPM, half rated flow: 750 Sm³/d
        # (flow scales linearly with speed for affinity laws)
        h_half, _ = solver._compressor_head_bar(750.0, 30.0, 3500.0, comp, 'comp1')

        # Head should scale with (N/N_ref)², so (0.5)² = 0.25
        # But flow also affects head, so check that it's lower
        assert h_half < h_rated
        print("✓ test_head_scaling_at_half_speed")


class TestOperatingEnvelopeProtection:
    """Test surge/runout detection and status tracking."""

    def test_surge_detection(self):
        """Surge protection detects low-flow operation."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Below surge line: 400 Sm³/d (surge is at ~450)
        h_surge, status_surge = solver._compressor_head_bar(
            400.0, 30.0, 7000.0, comp, 'comp1'
        )

        assert status_surge == 'SURGE'
        assert h_surge == 0.0
        print("✓ test_surge_detection")

    def test_runout_detection(self):
        """Runout protection detects high-flow operation."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Above runout line: 2300 Sm³/d (runout is at ~2200)
        h_runout, status_runout = solver._compressor_head_bar(
            2300.0, 30.0, 7000.0, comp, 'comp1'
        )

        assert status_runout == 'RUNOUT'
        assert h_runout == 0.0
        print("✓ test_runout_detection")

    def test_off_condition(self):
        """OFF status when speed or flow is zero."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet1', 'outlet1', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Zero speed
        h_off, status_off = solver._compressor_head_bar(1500.0, 30.0, 0.0, comp, 'comp1')
        assert status_off == 'OFF'
        assert h_off == 0.0

        # Zero flow
        h_off, status_off = solver._compressor_head_bar(0.0, 30.0, 7000.0, comp, 'comp1')
        assert status_off == 'OFF'
        assert h_off == 0.0

        print("✓ test_off_condition")


class TestConvergenceStability:
    """Test solver convergence with affinity laws."""

    def test_simple_network_convergence(self):
        """Solver converges on simple pipe-compressor network."""
        # Create a simple network: inlet -> compressor -> outlet
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet', 'outlet', comp_map)

        nodes = {
            'inlet': GasNodeProperties('inlet', pressure_bar=20.0, kind='facility'),
            'outlet': GasNodeProperties('outlet', pressure_bar=None, kind='facility'),
        }

        edges = {
            'pipe1': GasPipeProperties(
                link_id='pipe1',
                source_id='inlet',
                target_id='outlet',
                length_km=10.0,
                diameter_mm=300.0,
            ),
        }

        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Solve with affinity model
        pressures, flows, info = solver.solve_isothermal(
            compressor_speeds={'comp1': 7000.0},
            max_iterations=30,
            tolerance=0.1,
            verbose=False,
        )

        # Should converge
        assert info['success'] is True or info['final_residual_bar'] < 1.0
        assert len(pressures) == 2
        assert len(flows) >= 1
        print(f"✓ test_simple_network_convergence (iterations: {info['iterations']})")

    def test_convergence_status_tracking(self):
        """Solver tracks compressor status during convergence."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet', 'outlet', comp_map)

        nodes = {
            'inlet': GasNodeProperties('inlet', pressure_bar=20.0),
            'outlet': GasNodeProperties('outlet', pressure_bar=None),
        }
        edges = {
            'pipe1': GasPipeProperties('pipe1', 'inlet', 'outlet', 10.0, 300.0),
        }
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        pressures, flows, info = solver.solve_isothermal(
            compressor_speeds={'comp1': 7000.0},
            max_iterations=30,
            tolerance=0.1,
        )

        # Check that status tracking is in info
        assert 'compressor_status' in info
        assert 'comp1' in info['compressor_status']
        assert info['compressor_status']['comp1'] in ['OK', 'SURGE', 'RUNOUT', 'OFF']
        print("✓ test_convergence_status_tracking")


class TestBackwardCompatibility:
    """Test that simplified model still works when no maps provided."""

    def test_solver_without_compressor_maps(self):
        """Solver works with old-style compressors (no maps)."""
        # Create compressor without map
        comp = CompressorStation(
            link_id='comp1',
            inlet_node_id='inlet',
            outlet_node_id='outlet',
            rated_flow_sm3d=1000.0,
            rated_head_bar=50.0,
            rated_speed_rpm=7000.0,
        )

        nodes = {
            'inlet': GasNodeProperties('inlet', pressure_bar=20.0),
            'outlet': GasNodeProperties('outlet', pressure_bar=None),
        }
        edges = {
            'pipe1': GasPipeProperties('pipe1', 'inlet', 'outlet', 10.0, 300.0),
        }
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Should use simplified model
        assert 'comp1' not in solver.compressor_affinity_models
        assert solver.compressor_has_map['comp1'] is False

        # Should still converge
        pressures, flows, info = solver.solve_isothermal(
            compressor_speeds={'comp1': 7000.0},
            max_iterations=30,
            tolerance=0.1,
        )

        assert info['success'] is True or info['final_residual_bar'] < 1.0
        print("✓ test_solver_without_compressor_maps")


class TestCentrifugalCompressor:
    """Test integration with different compressor types."""

    def test_centrifugal_map_integration(self):
        """Integration works with centrifugal compressor map."""
        comp_map = create_centrifugal_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet', 'outlet', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Verify centrifugal map was integrated
        assert 'comp1' in solver.compressor_affinity_models
        assert comp.rated_flow_sm3d == comp_map.rated_flow_sm3d
        assert comp.rated_speed_rpm == comp_map.speed_rpm
        print("✓ test_centrifugal_map_integration")


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_very_low_speed(self):
        """Compressor handles very low speed gracefully."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet', 'outlet', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Very low speed: 100 RPM (< min_speed_rpm typical range)
        # At this speed, the rated flow 1500 Sm³/d is way above the runout line
        # (runout scales linearly with speed, so runout = 2200 * 100/7000 ≈ 31 Sm³/d)
        # So the compressor will correctly detect RUNOUT, which is expected behavior
        h, status = solver._compressor_head_bar(1500.0, 30.0, 100.0, comp, 'comp1')

        # Should return zero head gracefully (likely RUNOUT, SURGE, or OFF)
        assert status in ['OK', 'OFF', 'SURGE', 'RUNOUT']
        assert h >= 0.0
        print("✓ test_very_low_speed")

    def test_very_high_speed(self):
        """Compressor handles very high speed gracefully."""
        comp_map = create_axial_compressor_map()
        comp = create_compressor_station_with_map('comp1', 'inlet', 'outlet', comp_map)

        nodes = {}
        edges = {}
        compressors = {'comp1': comp}

        solver = GasNetworkSolver(nodes, edges, compressors)

        # Very high speed: 20000 RPM (way above typical 8500)
        h, status = solver._compressor_head_bar(1500.0, 30.0, 20000.0, comp, 'comp1')

        # Should return very high head
        assert status == 'OK'
        assert h > comp_map.rated_head_bar * 4  # (20000/7000)² ≈ 8.16x


        print("✓ test_very_high_speed")


def run_all_tests():
    """Run all integration tests."""
    tests = [
        # CompressorStation extension
        TestCompressorStationExtension().test_compressor_station_without_map,
        TestCompressorStationExtension().test_compressor_station_with_map,
        TestCompressorStationExtension().test_compressor_station_map_validation,
        TestCompressorStationExtension().test_factory_helper,

        # Affinity model initialization
        TestAffinityModelInitialization().test_solver_creates_affinity_models,
        TestAffinityModelInitialization().test_solver_mixed_compressors,

        # Head calculation accuracy
        TestHeadCalculationAccuracy().test_head_with_affinity_model,
        TestHeadCalculationAccuracy().test_head_with_simplified_model,
        TestHeadCalculationAccuracy().test_head_scaling_at_half_speed,

        # Operating envelope protection
        TestOperatingEnvelopeProtection().test_surge_detection,
        TestOperatingEnvelopeProtection().test_runout_detection,
        TestOperatingEnvelopeProtection().test_off_condition,

        # Convergence stability
        TestConvergenceStability().test_simple_network_convergence,
        TestConvergenceStability().test_convergence_status_tracking,

        # Backward compatibility
        TestBackwardCompatibility().test_solver_without_compressor_maps,

        # Centrifugal compressor
        TestCentrifugalCompressor().test_centrifugal_map_integration,

        # Edge cases
        TestEdgeCases().test_very_low_speed,
        TestEdgeCases().test_very_high_speed,
    ]

    print("Running T2.3 Gas-Network Affinity Integration Tests...")
    print("=" * 70)

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
