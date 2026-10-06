"""Unit tests for Compressor Affinity Laws and Performance Mapping (v31).

Tests affinity law scaling, efficiency curves, manufacturer map interpolation,
and multi-speed performance prediction.
"""

import pytest
import numpy as np
from optimization.compressor_affinity import (
    CompressorMap,
    CompressorAffinityModel,
    create_axial_compressor_map,
    create_centrifugal_compressor_map,
)


class TestCompressorMapBasics:
    """Test basic CompressorMap functionality."""

    def test_compressor_map_creation(self):
        """Test CompressorMap initializes correctly."""
        comp_map = create_axial_compressor_map()

        assert comp_map.speed_rpm == 7000.0
        assert comp_map.rated_flow_sm3d == 1500.0
        assert comp_map.rated_head_bar == 42.0
        assert len(comp_map.design_flows) == 6
        assert len(comp_map.design_heads) == 6
        assert len(comp_map.design_efficiencies) == 6

    def test_compressor_map_validation(self):
        """Test CompressorMap validates envelope constraints."""
        # Valid map
        valid_map = CompressorMap(
            speed_rpm=7000.0,
            rated_flow_sm3d=1500.0,
            rated_head_bar=42.0,
            design_flows=[600.0, 1500.0],
            design_heads=[55.0, 42.0],
            design_efficiencies=[0.75, 0.80],
            surge_flow_sm3d=450.0,
            runout_flow_sm3d=2200.0,
        )
        assert valid_map.rated_flow_sm3d > valid_map.surge_flow_sm3d
        assert valid_map.rated_flow_sm3d < valid_map.runout_flow_sm3d

        # Invalid envelope: surge >= rated
        with pytest.raises(ValueError):
            CompressorMap(
                speed_rpm=7000.0,
                rated_flow_sm3d=1500.0,
                rated_head_bar=42.0,
                design_flows=[600.0, 1500.0],
                design_heads=[55.0, 42.0],
                design_efficiencies=[0.75, 0.80],
                surge_flow_sm3d=1600.0,  # > rated
                runout_flow_sm3d=2200.0,
            )

    def test_head_interpolation(self):
        """Test head interpolation at intermediate flows."""
        comp_map = create_axial_compressor_map()

        # At design points
        h_1500 = comp_map.get_head(1500.0)
        assert h_1500 == 42.0

        # At intermediate point (1250 is between 1200 and 1500 design points)
        h_1250 = comp_map.get_head(1250.0)
        assert 42.0 < h_1250 < 48.0  # Between 48 at 1200 and 42 at 1500

        # Outside range
        h_out = comp_map.get_head(3000.0)
        assert h_out is None

    def test_efficiency_interpolation(self):
        """Test efficiency interpolation at intermediate flows."""
        comp_map = create_axial_compressor_map()

        # At design points
        eff_1500 = comp_map.get_efficiency(1500.0)
        assert eff_1500 == 0.80

        # TEST FIX: 1200 Sm3/d is itself a design point of the axial map
        # (efficiencies 0.72, 0.78, 0.82, 0.80, ... at 600, 900, 1200, 1500), and it
        # is the best-efficiency point, so linear interpolation returns exactly 0.82;
        # the old strict "< 0.82" bound could never hold. Check the design point
        # exactly, then check a genuinely intermediate flow (1350, midway between
        # 0.82 and 0.80 -> 0.81), which must lie strictly between its neighbours.
        eff_1200 = comp_map.get_efficiency(1200.0)
        assert abs(eff_1200 - 0.82) < 1e-9
        eff_1350 = comp_map.get_efficiency(1350.0)
        assert 0.80 < eff_1350 < 0.82
        assert abs(eff_1350 - 0.81) < 1e-9

        # Outside range
        eff_out = comp_map.get_efficiency(3000.0)
        assert eff_out is None


class TestAffinityLawScaling:
    """Test affinity law scaling relationships."""

    def test_head_scaling_with_speed(self):
        """Test head scaling: H ∝ (N/N_ref)²"""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Reference speed: 7000 RPM, rated head = 42 bar
        h_ref = comp.scale_head(42.0, 7000.0, 7000.0)
        assert h_ref == 42.0

        # Half speed: should be 1/4 of head
        h_half = comp.scale_head(42.0, 7000.0, 3500.0)
        assert abs(h_half - 10.5) < 0.01

        # Double speed: should be 4x of head
        h_double = comp.scale_head(42.0, 7000.0, 14000.0)
        assert abs(h_double - 168.0) < 0.1

        # Verify ratio: (N2/N1)² = (H2/H1)
        ratio_speed = 3500.0 / 7000.0
        ratio_head = h_half / h_ref
        assert abs(ratio_head - ratio_speed**2) < 0.001

    def test_flow_scaling_with_speed(self):
        """Test flow scaling: Q ∝ (N/N_ref)"""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Reference speed
        q_ref = comp.scale_flow(1500.0, 7000.0, 7000.0)
        assert q_ref == 1500.0

        # Half speed: should be 1/2 of flow
        q_half = comp.scale_flow(1500.0, 7000.0, 3500.0)
        assert abs(q_half - 750.0) < 0.01

        # Double speed: should be 2x of flow
        q_double = comp.scale_flow(1500.0, 7000.0, 14000.0)
        assert abs(q_double - 3000.0) < 0.1

        # Verify ratio: (N2/N1) = (Q2/Q1)
        ratio_speed = 3500.0 / 7000.0
        ratio_flow = q_half / q_ref
        assert abs(ratio_flow - ratio_speed) < 0.001

    def test_power_scaling_with_speed(self):
        """Test power scaling: P ∝ (N/N_ref)³ * (ρ/ρ_ref)"""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Assume 100 kW at reference conditions (isothermal, density ratio = 1.0)
        p_ref = 100.0

        # Half speed, same density
        p_half = comp.scale_power(p_ref, 7000.0, 3500.0, density_ratio=1.0)
        assert abs(p_half - 12.5) < 0.1  # (1/2)³ * 100 = 12.5

        # Double speed, same density
        p_double = comp.scale_power(p_ref, 7000.0, 14000.0, density_ratio=1.0)
        assert abs(p_double - 800.0) < 1.0  # 2³ * 100 = 800

        # Same speed, higher density ratio (e.g., cooler gas)
        p_dense = comp.scale_power(p_ref, 7000.0, 7000.0, density_ratio=1.1)
        assert abs(p_dense - 110.0) < 0.1

        # Verify ratio: (N2/N1)³ = (P2/P1) when density constant
        ratio_speed = 3500.0 / 7000.0
        ratio_power = p_half / p_ref
        assert abs(ratio_power - ratio_speed**3) < 0.001


class TestEfficiencyScaling:
    """Test efficiency estimation near design point."""

    def test_efficiency_near_design_point(self):
        """Test efficiency remains high near design point (85-105% flow)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Get reference efficiency at rated flow
        eff_ref = ref_map.get_efficiency(1500.0)
        assert eff_ref == 0.80

        # At 90% of design
        eff_90 = comp.scale_efficiency(1500.0, 1350.0)
        assert eff_90 > 0.95  # Should be high near design

        # At 100% of design
        eff_100 = comp.scale_efficiency(1500.0, 1500.0)
        assert 0.98 <= eff_100 <= 1.01  # Should be near 1.0 (or slightly above)

        # At 105% of design
        eff_105 = comp.scale_efficiency(1500.0, 1575.0)
        assert eff_105 > 0.95  # Should still be high

    def test_efficiency_at_surge(self):
        """Test efficiency degradation approaching surge (low flow)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Near surge: 50% of design
        eff_surge = comp.scale_efficiency(1500.0, 750.0)
        assert 0.70 <= eff_surge <= 0.85  # Should drop but not to zero

    def test_efficiency_at_runout(self):
        """Test efficiency degradation approaching runout (high flow)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Near runout: 130% of design
        eff_runout = comp.scale_efficiency(1500.0, 1950.0)
        assert 0.75 <= eff_runout <= 0.85  # Should drop but not to zero


class TestPerformanceAtSpeed:
    """Test get_performance_at_speed method."""

    def test_rated_conditions(self):
        """Test performance at rated speed and flow."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        h, eff, p, status = comp.get_performance_at_speed(7000.0, 1500.0)

        assert status == 'OK'
        assert abs(h - 42.0) < 0.1  # Should match rated head
        assert abs(eff - 0.80) < 0.05  # Should match rated efficiency
        assert p > 0  # Power should be positive

    def test_half_speed_operation(self):
        """Test performance at half speed with scaled flow."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # At half speed with half rated flow
        h, eff, p, status = comp.get_performance_at_speed(3500.0, 750.0)

        assert status == 'OK'
        assert abs(h - 10.5) < 0.1  # H ∝ N²: 42 * (1/2)² = 10.5
        assert p > 0

    def test_surge_protection(self):
        """Test compressor stall protection at low flow (surge)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Scale surge point to 7000 RPM (already at reference speed)
        surge_flow = ref_map.surge_flow_sm3d  # 450 Sm³/d

        # Just below surge
        h, eff, p, status = comp.get_performance_at_speed(7000.0, surge_flow - 10.0)
        assert status == 'SURGE'
        assert h is None

    def test_runout_protection(self):
        """Test compressor choke protection at high flow (runout)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Scale runout point to 7000 RPM
        runout_flow = ref_map.runout_flow_sm3d  # 2200 Sm³/d

        # Just above runout
        h, eff, p, status = comp.get_performance_at_speed(7000.0, runout_flow + 10.0)
        assert status == 'RUNOUT'
        assert h is None

    def test_off_conditions(self):
        """Test compressor off (zero speed or flow)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Zero speed
        h, eff, p, status = comp.get_performance_at_speed(0.0, 1500.0)
        assert status == 'OFF'
        assert h is None

        # Zero flow
        h, eff, p, status = comp.get_performance_at_speed(7000.0, 0.0)
        assert status == 'OFF'
        assert h is None


class TestOperatingMap:
    """Test operating_map generation at various speeds."""

    def test_operating_map_at_rated_speed(self):
        """Test operating map generation at rated speed."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Extend range to 2300 to capture runout points (runout at 2200 Sm³/d)
        op_map = comp.operating_map(
            target_speed_rpm=7000.0,
            flow_range=(400.0, 2300.0),
            n_points=50,
        )

        assert op_map['speed_rpm'] == 7000.0
        assert len(op_map['flows']) == 50
        assert len(op_map['heads']) == 50
        assert len(op_map['efficiencies']) == 50
        assert len(op_map['powers']) == 50
        assert len(op_map['statuses']) == 50

        # Check that surge points are marked
        surge_count = sum(1 for s in op_map['statuses'] if s == 'SURGE')
        assert surge_count > 0  # Should have surge points at low flow

        # Check that runout points are marked
        runout_count = sum(1 for s in op_map['statuses'] if s == 'RUNOUT')
        assert runout_count > 0  # Should have runout points at high flow

        # Check that OK points exist in middle
        ok_count = sum(1 for s in op_map['statuses'] if s == 'OK')
        assert ok_count > 25  # Majority should be OK

    def test_operating_map_at_half_speed(self):
        """Test operating map at half speed."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        op_map_half = comp.operating_map(
            target_speed_rpm=3500.0,
            flow_range=(200.0, 1100.0),
            n_points=50,
        )

        # At half speed, head should be 1/4 of rated speed head
        # Find an OK point at similar flow ratio
        for i, (q, h, s) in enumerate(zip(
            op_map_half['flows'],
            op_map_half['heads'],
            op_map_half['statuses'],
        )):
            if s == 'OK' and abs(q - 750.0) < 50:  # Near half of 1500
                # Head should be ~1/4 of rated (42 bar at 1500 flow, 7000 rpm)
                assert h < 15  # Half-speed map should have lower head
                break

    def test_operating_map_monotonicity(self):
        """Test that head decreases with increasing flow (typical curve)."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        op_map = comp.operating_map(
            target_speed_rpm=7000.0,
            flow_range=(600.0, 1900.0),
            n_points=40,
        )

        # Extract OK points only
        ok_points = [
            (q, h) for q, h, s in zip(
                op_map['flows'],
                op_map['heads'],
                op_map['statuses'],
            )
            if s == 'OK'
        ]

        # Verify head generally decreases with flow (within OK region)
        if len(ok_points) > 1:
            for i in range(len(ok_points) - 1):
                q1, h1 = ok_points[i]
                q2, h2 = ok_points[i + 1]
                if q2 > q1:  # If next point is higher flow
                    # Head should be lower (allowing some tolerance for interpolation noise)
                    assert h2 <= h1 + 1.0


class TestOptimizationEnvelope:
    """Test optimization envelope generation (surge/runout lines)."""

    def test_optimization_envelope_generation(self):
        """Test optimization envelope for speed optimization."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        envelope = comp.optimization_envelope(
            speed_range=(3500.0, 10500.0),
            n_speeds=7,
        )

        assert len(envelope) == 7
        assert all('speed_rpm' in e for e in envelope)
        assert all('surge_flow_sm3d' in e for e in envelope)
        assert all('runout_flow_sm3d' in e for e in envelope)
        assert all('surge_head_bar' in e for e in envelope)

    def test_envelope_surge_scaling(self):
        """Test that surge line scales correctly with speed."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        envelope = comp.optimization_envelope(
            speed_range=(3500.0, 7000.0),
            n_speeds=3,
        )

        # At 3500 RPM (half speed)
        env_half = envelope[0]
        assert abs(env_half['speed_rpm'] - 3500.0) < 1.0
        surge_half = env_half['surge_flow_sm3d']

        # At 7000 RPM (rated speed)
        env_rated = envelope[-1]
        assert abs(env_rated['speed_rpm'] - 7000.0) < 1.0
        surge_rated = env_rated['surge_flow_sm3d']

        # Surge should scale linearly with speed: Q ∝ N
        expected_ratio = 7000.0 / 3500.0
        actual_ratio = surge_rated / surge_half
        assert abs(actual_ratio - expected_ratio) < 0.01

    def test_envelope_head_scaling(self):
        """Test that surge head scales with speed squared."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        envelope = comp.optimization_envelope(
            speed_range=(3500.0, 7000.0),
            n_speeds=3,
        )

        # At 3500 RPM
        env_half = envelope[0]
        surge_head_half = env_half['surge_head_bar']

        # At 7000 RPM
        env_rated = envelope[-1]
        surge_head_rated = env_rated['surge_head_bar']

        # Head should scale with N²
        expected_ratio = (7000.0 / 3500.0) ** 2
        actual_ratio = surge_head_rated / surge_head_half
        assert abs(actual_ratio - expected_ratio) < 0.1


class TestCentrifugalCompressor:
    """Test with centrifugal compressor map."""

    def test_centrifugal_map_creation(self):
        """Test centrifugal compressor map initializes."""
        comp_map = create_centrifugal_compressor_map()

        assert comp_map.speed_rpm == 8500.0
        assert comp_map.rated_flow_sm3d == 800.0
        assert comp_map.rated_head_bar == 65.0

    def test_centrifugal_affinity_scaling(self):
        """Test affinity laws on centrifugal map."""
        ref_map = create_centrifugal_compressor_map()
        comp = CompressorAffinityModel(ref_map, 8500.0)

        # At rated conditions
        h_ref, eff_ref, p_ref, status_ref = comp.get_performance_at_speed(8500.0, 800.0)
        assert status_ref == 'OK'
        assert abs(h_ref - 65.0) < 0.1

        # At 80% speed
        h_80, eff_80, p_80, status_80 = comp.get_performance_at_speed(6800.0, 640.0)
        assert status_80 == 'OK'
        # Head at 80% speed should be 0.64 times original
        expected_h_80 = 65.0 * (6800.0 / 8500.0) ** 2
        assert abs(h_80 - expected_h_80) < 1.0


class TestScenariosEvaluated:
    """Test scenario tracking."""

    def test_scenarios_evaluated_counter(self):
        """Test that scenarios_evaluated counter increments."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        initial = comp.scenarios_evaluated
        assert initial == 0

        # Generate operating map
        comp.operating_map(7000.0, n_points=20)

        # Should have evaluated 20 points
        assert comp.scenarios_evaluated == 20


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_low_speed(self):
        """Test behavior at very low speed."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        h, eff, p, status = comp.get_performance_at_speed(500.0, 50.0)
        # Should operate but with very low head
        if status == 'OK':
            assert h > 0

    def test_very_high_speed(self):
        """Test behavior at very high speed."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        h, eff, p, status = comp.get_performance_at_speed(20000.0, 4300.0)
        # Should handle high speed (h ∝ N²)
        if status == 'OK':
            assert h > 100  # Should be high head

    def test_negative_inputs_rejected(self):
        """Test that negative inputs are handled gracefully."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Negative head should return 0
        h = comp.scale_head(-10.0, 7000.0, 3500.0)
        assert h == 0.0

        # Negative flow should return 0
        q = comp.scale_flow(-100.0, 7000.0, 3500.0)
        assert q == 0.0

        # Negative power should return 0
        p = comp.scale_power(-50.0, 7000.0, 3500.0)
        assert p == 0.0

    def test_zero_reference_speed(self):
        """Test handling of zero reference speed."""
        ref_map = create_axial_compressor_map()
        comp = CompressorAffinityModel(ref_map, 7000.0)

        # Division by zero should be handled
        h = comp.scale_head(42.0, 0.0, 7000.0)
        assert h == 0.0

        q = comp.scale_flow(1500.0, 0.0, 7000.0)
        assert q == 0.0

        p = comp.scale_power(100.0, 0.0, 7000.0)
        assert p == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
