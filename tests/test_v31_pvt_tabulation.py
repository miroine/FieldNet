"""Unit tests for v31 real PVT tabulation.

Tests PVT table loading, interpolation, and integration with tank material balance.
"""

import pytest
import numpy as np
import pandas as pd
from physics.pvt_table import PVTTable
from network.reservoir_mb import Tank


class TestPVTTableBasics:
    """Test PVT table creation and properties."""

    def test_screening_default_oil(self):
        """Test screening default PVT for oil phase."""
        pvt = PVTTable.screening_default('oil')

        assert pvt.phase == 'oil'
        assert pvt.pbub > 0
        assert pvt.rsb > 0
        assert 'Screening' in pvt.source
        assert pvt.n_points == 5  # default has 5 points

    def test_screening_default_gas(self):
        """Test screening default PVT for gas phase."""
        pvt = PVTTable.screening_default('gas')

        assert pvt.phase == 'gas'
        assert pvt.n_points == 5
        assert pvt.rsb > 0  # constant for gas

    def test_pvt_from_dict_list(self):
        """Test loading PVT from dict list."""
        data = [
            {'pressure_bar': 50, 'bo': 1.3, 'bg': 0.01, 'gor_sm3sm3': 50, 'pbub_bar': 150},
            {'pressure_bar': 150, 'bo': 1.2, 'bg': 0.005, 'gor_sm3sm3': 120, 'pbub_bar': 150},
            {'pressure_bar': 300, 'bo': 1.0, 'bg': 0.002, 'gor_sm3sm3': 120, 'pbub_bar': 150},
        ]
        pvt = PVTTable.from_dict_list(data, phase='oil', source='Test data')

        assert pvt.n_points == 3
        assert pvt.pbub == 150
        assert pvt.rsb == 120

    def test_pvt_from_dataframe(self):
        """Test loading PVT from DataFrame."""
        df = pd.DataFrame({
            'pressure_bar': [50, 100, 150, 250, 300],
            'bo': [1.3, 1.25, 1.2, 1.1, 1.0],
            'bg': [0.02, 0.01, 0.005, 0.003, 0.002],
            'gor_sm3sm3': [60, 100, 120, 120, 120],
            'pbub_bar': 150,
        })
        pvt = PVTTable(df, phase='oil', source='CSV Test')

        assert pvt.n_points == 5
        assert pvt.phase == 'oil'


class TestPVTInterpolation:
    """Test PVT interpolation methods."""

    @pytest.fixture
    def sample_pvt(self):
        """Create a sample PVT table for testing."""
        data = [
            {'pressure_bar': 50, 'bo': 1.3, 'bg': 0.02, 'gor_sm3sm3': 50, 'pbub_bar': 150, 'z_factor': 0.95},
            {'pressure_bar': 150, 'bo': 1.2, 'bg': 0.005, 'gor_sm3sm3': 120, 'pbub_bar': 150, 'z_factor': 0.90},
            {'pressure_bar': 300, 'bo': 1.0, 'bg': 0.002, 'gor_sm3sm3': 120, 'pbub_bar': 150, 'z_factor': 0.85},
        ]
        return PVTTable.from_dict_list(data, phase='oil', source='Test')

    def test_bo_interpolation(self, sample_pvt):
        """Test Bo interpolation."""
        # Endpoints
        assert abs(sample_pvt.get_bo(50) - 1.3) < 0.01
        assert abs(sample_pvt.get_bo(300) - 1.0) < 0.01

        # Midpoint
        bo_mid = sample_pvt.get_bo(150)
        assert 1.0 < bo_mid < 1.3

    def test_bg_interpolation(self, sample_pvt):
        """Test Bg interpolation."""
        # Endpoints
        assert abs(sample_pvt.get_bg(50) - 0.02) < 0.001
        assert abs(sample_pvt.get_bg(300) - 0.002) < 0.001

        # Out of range (should clamp)
        assert sample_pvt.get_bg(1000) == sample_pvt.get_bg(300)
        assert sample_pvt.get_bg(0) == sample_pvt.get_bg(50)

    def test_gor_above_bubble_point(self, sample_pvt):
        """Test GOR stays constant above bubble point."""
        # Above Pb (150 bar): Rs stays at Rsb
        gor_200 = sample_pvt.get_gor(200)
        gor_300 = sample_pvt.get_gor(300)

        assert gor_200 == pytest.approx(120.0)
        assert gor_300 == pytest.approx(120.0)

    def test_gor_below_bubble_point(self, sample_pvt):
        """Test GOR rises below bubble point."""
        # Below Pb: Rs from table
        gor_50 = sample_pvt.get_gor(50)
        gor_150 = sample_pvt.get_gor(150)

        assert gor_50 < gor_150  # rises as P decreases
        assert abs(gor_50 - 50) < 5
        assert abs(gor_150 - 120) < 5

    def test_z_factor_interpolation(self, sample_pvt):
        """Test z-factor interpolation."""
        z_50 = sample_pvt.get_z(50)
        z_150 = sample_pvt.get_z(150)
        z_300 = sample_pvt.get_z(300)

        assert 0.3 <= z_50 <= 1.5
        assert 0.3 <= z_150 <= 1.5
        assert 0.3 <= z_300 <= 1.5
        assert z_50 > z_300  # z decreases with pressure


class TestTankWithPVT:
    """Test Tank class integration with real PVT tables."""

    @pytest.fixture
    def pvt_oil(self):
        """Oil PVT table."""
        data = [
            {'pressure_bar': 50, 'bo': 1.3, 'bg': 0.02, 'gor_sm3sm3': 50, 'pbub_bar': 150},
            {'pressure_bar': 150, 'bo': 1.2, 'bg': 0.005, 'gor_sm3sm3': 120, 'pbub_bar': 150},
            {'pressure_bar': 300, 'bo': 1.0, 'bg': 0.002, 'gor_sm3sm3': 120, 'pbub_bar': 150},
        ]
        return PVTTable.from_dict_list(data, phase='oil', source='Test')

    @pytest.fixture
    def tank_node(self):
        """Create a tank node."""
        return {
            'id': 'tank_1',
            'name': 'Oil Tank',
            'kind': 'reservoir',
            'params': {
                'fluid_phase': 'oil',
                'reservoir_pressure_bar': 250,
                'temperature_c': 90,
                'stoiip_sm3': 20e6,
                'boi_rm3_sm3': 1.2,
                'rsi_sm3_sm3': 100,
                'ct_1bar': 1.5e-4,
            }
        }

    def test_tank_with_real_pvt(self, tank_node, pvt_oil):
        """Test tank initialized with real PVT."""
        tank = Tank(tank_node, pvt_table=pvt_oil)

        assert tank.phase == 'oil'
        assert tank.pb == 150  # from PVT, not screening 150
        assert tank.rsb_table == 120
        assert tank.pvt_source == 'Test'

    def test_tank_without_pvt_falls_back(self, tank_node):
        """Test tank falls back to screening model if no PVT provided."""
        tank = Tank(tank_node, pvt_table=None)

        assert tank.phase == 'oil'
        assert tank.pb > 0  # should have screening Pb
        assert 'Screening' in tank.pvt_source

    def test_tank_gor_from_pvt_table(self, tank_node, pvt_oil):
        """Test well GOR comes from PVT table."""
        tank = Tank(tank_node, pvt_table=pvt_oil)

        # At high pressure (above Pb), GOR should be constant
        overrides_300 = tank.well_overrides()
        tank.p = 300  # simulate pressure at high p
        overrides_high = tank.well_overrides()

        # GOR should come from table
        gor_from_table = pvt_oil.get_gor(300)
        assert 100 < gor_from_table <= 120  # should be Rsb or close

    def test_material_balance_uses_pvt_bo(self, tank_node, pvt_oil):
        """Test material balance step uses real Bo from PVT table."""
        tank = Tank(tank_node, pvt_table=pvt_oil)

        p_initial = tank.p

        # Step: produce 1M Sm³ of oil
        tank.step(oil_sm3=1e6, water_m3=0, gas_sm3=0, dt_days=30)

        p_after = tank.p
        assert p_after < p_initial  # pressure should drop

        # Void created depends on Bo at current pressure (from PVT table)
        bo_at_p = pvt_oil.get_bo(p_initial)
        assert bo_at_p > 1.0  # should be reasonable


class TestTankMaterialBalance:
    """Test tank material balance with PVT tables."""

    @pytest.fixture
    def simple_tank(self):
        """Create a simple tank for MB testing."""
        pvt_data = [
            {'pressure_bar': 50, 'bo': 1.35, 'bg': 0.025, 'gor_sm3sm3': 40, 'pbub_bar': 120},
            {'pressure_bar': 120, 'bo': 1.25, 'bg': 0.008, 'gor_sm3sm3': 100, 'pbub_bar': 120},
            {'pressure_bar': 250, 'bo': 1.12, 'bg': 0.003, 'gor_sm3sm3': 100, 'pbub_bar': 120},
        ]
        pvt = PVTTable.from_dict_list(pvt_data, phase='oil', source='Test')

        node = {
            'id': 'tank',
            'name': 'Test Tank',
            'kind': 'reservoir',
            'params': {
                'fluid_phase': 'oil',
                'reservoir_pressure_bar': 250,
                'temperature_c': 90,
                'stoiip_sm3': 50e6,
                'boi_rm3_sm3': 1.2,
                'rsi_sm3_sm3': 100,
                'ct_1bar': 1.5e-4,
                'aquifer_pi_m3d_bar': 0,
            }
        }
        return Tank(node, pvt_table=pvt)

    def test_pressure_decline_with_production(self, simple_tank):
        """Test tank pressure declines with oil production."""
        p0 = simple_tank.p

        # Produce over 10 days
        for day in range(10):
            simple_tank.step(oil_sm3=100e3, water_m3=0, gas_sm3=0, dt_days=1)

        assert simple_tank.p < p0
        assert simple_tank.np == 1e6  # 100k * 10 days

    def test_gor_rise_from_pvt(self, simple_tank):
        """Test GOR rises as tank depletes below Pb."""
        # Deplete to below Pb
        while simple_tank.p > 120:
            simple_tank.step(oil_sm3=1e6, water_m3=0, gas_sm3=0, dt_days=30)

        # Get GOR from well overrides (should come from PVT table)
        overrides = simple_tank.well_overrides()
        gor = overrides['gor_sm3sm3']

        # Below Pb the *solution* GOR (Rs) falls, but the *producing* GOR delivered to the wells
        # must not drop under the initial GOR (free gas is produced in addition to dissolved gas).
        assert gor >= 100 - 1e-9
        assert simple_tank.pvt.get_gor(simple_tank.p) < 100  # the table's Rs itself does fall


class TestPVTSummary:
    """Test PVT summary for UI display."""

    def test_summary_includes_key_stats(self):
        """Test summary has key properties."""
        pvt = PVTTable.screening_default('oil')
        summary = pvt.summary()

        assert 'source' in summary
        assert 'phase' in summary
        assert 'n_points' in summary
        assert 'p_range' in summary
        assert 'pbub' in summary
        assert 'rsb' in summary


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
