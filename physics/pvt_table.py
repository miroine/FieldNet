"""Real PVT tabulation from lab data or correlations (v31).

Replaces fixed black-oil screening model (Pb=150 bar, Rsb=120 Sm³/Sm³) with
actual lab data or correlation-generated tables.

Supported input formats:
  * CSV: Columns pressure_bar, temperature_c, bo, bg, bw, gor_sm3sm3, pbub_bar, visc_o_cps, visc_g_cps, z_factor
  * JSON: List of dictionaries with same keys
  * Eclipse PVTO/PVTG (future)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Union, Dict, List


@dataclass
class PVTPoint:
    """Single PVT data point at (P, T)"""
    pressure_bar: float
    temperature_c: float
    bo: float  # oil formation volume factor [rm³/Sm³]
    bg: float  # gas formation volume factor [rm³/Sm³]
    bw: float  # water formation volume factor [rm³/Sm³]
    gor: float  # solution gas-oil ratio [Sm³/Sm³]
    pbub: float  # bubble point [bar]
    visc_o: float  # oil viscosity [cP]
    visc_g: float  # gas viscosity [cP]
    visc_w: float  # water viscosity [cP]
    z_factor: float  # compressibility factor (gas)


class PVTTable:
    """Real PVT data from lab measurements or correlations.

    Provides interpolation of Bo, Bg, Bw, Rs, Pb, viscosities, and z-factor.

    Attributes:
        phase (str): 'oil', 'gas', or 'gas_condensate'
        pbub (float): Bubble point pressure [bar] from data
        rsb (float): Solution gas at bubble point [Sm³/Sm³] from data
        df (pd.DataFrame): Sorted PVT table
        source (str): Description of source (e.g. "HYSYS export", "Standing correlation")
    """

    def __init__(self, df: pd.DataFrame, phase: str = 'oil', source: str = 'Uploaded'):
        """
        Initialize from DataFrame.

        Args:
            df: DataFrame with columns [pressure_bar, temperature_c, bo, bg, bw, gor_sm3sm3,
                pbub_bar, visc_o_cps, visc_g_cps, visc_w_cps, z_factor]
            phase: 'oil', 'gas', or 'gas_condensate'
            source: Description of data source
        """
        self.phase = phase
        self.source = source

        # Validate required columns (viscosity is optional, needed for some correlations)
        required = ['pressure_bar', 'bo', 'bg', 'gor_sm3sm3']
        if phase == 'oil':
            required.append('pbub_bar')

        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        # Fill defaults for optional columns
        if 'temperature_c' not in df.columns:
            df['temperature_c'] = 15.0  # standard temperature
        if 'bw' not in df.columns:
            df['bw'] = 1.0
        if 'visc_w_cps' not in df.columns:
            df['visc_w_cps'] = 0.5
        if 'z_factor' not in df.columns:
            df['z_factor'] = 1.0
        if 'pbub_bar' not in df.columns:
            df['pbub_bar'] = df['pressure_bar'].min() * 0.6  # default estimate

        # Sort by pressure
        self.df = df.sort_values('pressure_bar').reset_index(drop=True)
        self.n_points = len(self.df)

        # Extract Pb, Rsb from data (not hardcoded screening values)
        # Pb is the pressure at which gas comes out of solution (first point typically)
        # Rsb is the solution gas at Pb
        if phase == 'oil':
            pbub_col = self.df['pbub_bar'].iloc[0]  # should be consistent
            self.pbub = pbub_col
            # Find Rsb at bubble point (highest Pb in table)
            at_pb = self.df[self.df['pressure_bar'] <= self.pbub].sort_values('pressure_bar', ascending=False)
            self.rsb = at_pb['gor_sm3sm3'].iloc[0] if len(at_pb) > 0 else self.df['gor_sm3sm3'].iloc[0]
        else:
            self.pbub = self.df['pbub_bar'].iloc[0] if 'pbub_bar' in self.df.columns else None
            self.rsb = self.df['gor_sm3sm3'].iloc[-1]  # GOR constant for gas

    @classmethod
    def from_csv(cls, filepath: str, phase: str = 'oil', source: str = None) -> 'PVTTable':
        """Load PVT table from CSV file.

        Args:
            filepath: Path to CSV
            phase: 'oil', 'gas', or 'gas_condensate'
            source: Optional description (defaults to filename)

        Returns:
            PVTTable instance
        """
        df = pd.read_csv(filepath)
        source = source or f"CSV: {filepath.split('/')[-1]}"
        return cls(df, phase=phase, source=source)

    @classmethod
    def from_dict_list(cls, data: List[Dict], phase: str = 'oil', source: str = 'JSON') -> 'PVTTable':
        """Load PVT table from list of dictionaries.

        Args:
            data: List of dicts with PVT columns
            phase: 'oil', 'gas', or 'gas_condensate'
            source: Description of source

        Returns:
            PVTTable instance
        """
        df = pd.DataFrame(data)
        return cls(df, phase=phase, source=source)

    @classmethod
    def screening_default(cls, phase: str = 'oil') -> 'PVTTable':
        """Fallback screening PVT (fixed Pb=150, Rsb=120) for legacy compatibility.

        Returns:
            PVTTable with fixed properties
        """
        if phase == 'oil':
            # Create 5-point screening table (P from 50 to 350 bar)
            p_range = np.array([50, 100, 150, 250, 350])
            data = {
                'pressure_bar': p_range,
                'temperature_c': 90.0,
                'bo': 1.0 + 0.02 * (1 - p_range / 150),  # simple correlation
                'bg': 0.005 + 0.01 / (p_range / 1.01325),
                'bw': 1.0,
                'gor_sm3sm3': 120 * (p_range / 150) ** 0.8,  # constant at bubble point
                'pbub_bar': 150.0,
                'visc_o_cps': 5.0,
                'visc_g_cps': 0.012,
                'visc_w_cps': 0.5,
                'z_factor': 0.9
            }
            return cls(pd.DataFrame(data), phase='oil', source='Screening (fixed Pb=150)')
        else:
            # Gas phase
            p_range = np.array([10, 50, 100, 200, 300])
            data = {
                'pressure_bar': p_range,
                'temperature_c': 70.0,
                'bo': 1.0,
                'bg': 0.01 * (150 / p_range),
                'bw': 1.0,
                'gor_sm3sm3': 10000.0,  # constant for dry gas
                'pbub_bar': None,
                'visc_o_cps': 0.1,
                'visc_g_cps': 0.015,
                'visc_w_cps': 0.5,
                'z_factor': 0.85
            }
            return cls(pd.DataFrame(data), phase='gas', source='Screening (gas)')

    def get_bo(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate oil formation volume factor at (P, T).

        Args:
            p_bar: Pressure [bar]
            t_c: Temperature [°C] (default: 15°C = standard condition)

        Returns:
            Bo [rm³/Sm³]
        """
        # Clamp pressure to table range
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())

        # Linear interpolation (temperature not yet supported in full correlation)
        return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['bo']))

    def get_bg(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate gas formation volume factor at (P, T).

        Args:
            p_bar: Pressure [bar]
            t_c: Temperature [°C]

        Returns:
            Bg [rm³/Sm³]
        """
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
        return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['bg']))

    def get_bw(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate water formation volume factor at (P, T).

        Returns:
            Bw [rm³/Sm³] (typically ~1.0)
        """
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
        return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['bw']))

    def get_gor(self, p_bar: float) -> float:
        """Interpolate solution gas-oil ratio at pressure.

        Above bubble point (P > Pb): Rs = constant = Rsb (all gas stays dissolved)
        Below bubble point (P < Pb): Rs rises with depletion drive

        Args:
            p_bar: Pressure [bar]

        Returns:
            Rs [Sm³/Sm³]
        """
        if self.phase == 'oil':
            # For oil, Rs is constant above Pb, rises (or stays flat) below Pb
            # depending on data
            if p_bar >= self.pbub:
                return self.rsb  # saturated at Pb, no additional solution
            else:
                # Below Pb, interpolate from table
                p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
                return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['gor_sm3sm3']))
        else:
            # Gas phase: GOR is constant (reciprocal of CGR)
            return float(self.df['gor_sm3sm3'].iloc[-1])

    def get_z(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate compressibility factor at (P, T).

        Returns:
            z (dimensionless)
        """
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
        z = float(np.interp(p_clamp, self.df['pressure_bar'], self.df['z_factor']))
        return np.clip(z, 0.3, 1.5)  # sanity bounds

    def get_visc_o(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate oil viscosity at (P, T).

        Returns:
            Oil viscosity [cP]
        """
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
        if 'visc_o_cps' not in self.df.columns:
            return 5.0  # default
        return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['visc_o_cps']))

    def get_visc_g(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate gas viscosity at (P, T).

        Returns:
            Gas viscosity [cP]
        """
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
        if 'visc_g_cps' not in self.df.columns:
            return 0.015  # default
        return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['visc_g_cps']))

    def get_visc_w(self, p_bar: float, t_c: float = 15.0) -> float:
        """Interpolate water viscosity at (P, T).

        Returns:
            Water viscosity [cP]
        """
        p_clamp = np.clip(p_bar, self.df['pressure_bar'].min(), self.df['pressure_bar'].max())
        if 'visc_w_cps' not in self.df.columns:
            return 0.5  # default
        return float(np.interp(p_clamp, self.df['pressure_bar'], self.df['visc_w_cps']))

    def summary(self) -> Dict:
        """Summary statistics for UI display.

        Returns:
            Dict with key PVT properties
        """
        return {
            'source': self.source,
            'phase': self.phase,
            'n_points': self.n_points,
            'p_range': f"{self.df['pressure_bar'].min():.1f}–{self.df['pressure_bar'].max():.1f} bar",
            'pbub': f"{self.pbub:.1f} bar" if self.pbub else "N/A",
            'rsb': f"{self.rsb:.1f} Sm³/Sm³" if self.rsb else "N/A",
            't_range': f"{self.df['temperature_c'].min():.1f}–{self.df['temperature_c'].max():.1f} °C"
        }
