"""Relative permeability (oil-water and gas-oil), fractional flow and Buckley-Leverett helpers.

Pure functions / classes, no I/O, no Streamlit.  Stored on a reservoir node as ``params['relperm']``::

    {'model': 'corey' | 'let' | 'table',
     'swc': 0.2, 'sorw': 0.25, 'krw_max': 0.35, 'kro_max': 0.9,         # end points
     'nw': 2.5, 'no': 2.0,                                               # Corey exponents
     'Lw': .., 'Ew': .., 'Tw': .., 'Lo': .., 'Eo': .., 'To': ..,         # LET (water / oil)
     'table': [{'sw':.., 'krw':.., 'kro':..}, ...],                      # tabulated
     'mu_o_cp': 2.0, 'mu_w_cp': 0.5, 'mu_g_cp': 0.03,
     'gas': {'sgc':0.05,'sorg':0.15,'krg_max':0.8,'ng':2.0,'nog':2.0}}   # optional gas-oil (Corey)

Saturations are fractions; viscosities in cP; krw_max is krw at Sw = 1 - sorw, kro_max is kro at Sw = swc.
Nomenclature: Swn = (Sw - swc) / (1 - swc - sorw).
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

MODELS = ('corey', 'let', 'table')

DEFAULT_RELPERM = {
    'model': 'corey', 'swc': 0.2, 'sorw': 0.25, 'krw_max': 0.35, 'kro_max': 0.9, 'nw': 2.5, 'no': 2.0,
    'mu_o_cp': 2.0, 'mu_w_cp': 0.5, 'mu_g_cp': 0.03,
}
_LET_DEFAULT = {'Lw': 2.0, 'Ew': 1.0, 'Tw': 2.0, 'Lo': 2.0, 'Eo': 1.0, 'To': 2.0}
_GAS_DEFAULT = {'sgc': 0.05, 'sorg': 0.15, 'krg_max': 0.8, 'ng': 2.0, 'nog': 2.0}


def relperm_defaults(model: str = 'corey') -> dict:
    """Editable default parameter dict for ``model`` ('corey', 'let' or 'table')."""
    m = str(model).lower()
    if m not in MODELS:
        raise ValueError(f"Unknown relperm model '{model}'. Choose one of {', '.join(MODELS)}.")
    d = dict(DEFAULT_RELPERM); d['model'] = m
    if m == 'let':
        d.update(_LET_DEFAULT)
    elif m == 'table':
        rp = RelPerm.from_params(dict(DEFAULT_RELPERM))
        sw = np.linspace(d['swc'], 1 - d['sorw'], 8)
        d['table'] = [{'sw': round(float(s), 4), 'krw': round(float(rp.krw(s)), 5), 'kro': round(float(rp.kro(s)), 5)} for s in sw]
    return d


def _num(d, key, default=None, lo=None, hi=None, required=False):
    v = d.get(key, None)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required and default is None:
            raise ValueError(f"Relative permeability: parameter '{key}' is required.")
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Relative permeability: '{key}' must be a number, got {v!r}.")
    if not math.isfinite(v):
        raise ValueError(f"Relative permeability: '{key}' must be finite.")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise ValueError(f"Relative permeability: '{key}'={v:g} outside allowed range [{lo}, {hi}].")
    return v


def _clip01(x):
    return np.clip(np.asarray(x, dtype=float), 0.0, 1.0)


class RelPerm:
    """Oil-water relative permeability (+ optional gas-oil) with fractional-flow helpers."""

    def __init__(self, params: dict):
        d = dict(params or {})
        m = str(d.get('model', 'corey')).lower()
        if m not in MODELS:
            raise ValueError(f"Unknown relperm model '{d.get('model')}'. Choose one of {', '.join(MODELS)}.")
        self.model = m
        self.mu_o = _num(d, 'mu_o_cp', DEFAULT_RELPERM['mu_o_cp'], lo=1e-4)
        self.mu_w = _num(d, 'mu_w_cp', DEFAULT_RELPERM['mu_w_cp'], lo=1e-4)
        self.mu_g = _num(d, 'mu_g_cp', DEFAULT_RELPERM['mu_g_cp'], lo=1e-5)
        if m == 'table':
            self._init_table(d.get('table'))
        else:
            swc = _num(d, 'swc', d.get('swi', DEFAULT_RELPERM['swc']), lo=0.0, hi=0.95)
            sorw = _num(d, 'sorw', DEFAULT_RELPERM['sorw'], lo=0.0, hi=0.95)
            if swc + sorw >= 0.999:
                raise ValueError(f"Relative permeability: swc + sorw = {swc + sorw:.3f} leaves no mobile saturation range (must be < 1).")
            self.swc, self.sorw = swc, sorw
            self.krw_max = _num(d, 'krw_max', DEFAULT_RELPERM['krw_max'], lo=1e-6, hi=1.0)
            self.kro_max = _num(d, 'kro_max', DEFAULT_RELPERM['kro_max'], lo=1e-6, hi=1.0)
            if m == 'corey':
                self.nw = _num(d, 'nw', DEFAULT_RELPERM['nw'], lo=0.1, hi=20.0)
                self.no = _num(d, 'no', DEFAULT_RELPERM['no'], lo=0.1, hi=20.0)
            else:
                self.let = {k: _num(d, k, v, lo=0.01 if k[0] in 'LT' else 1e-4, hi=50.0) for k, v in _LET_DEFAULT.items()}
        g = d.get('gas')
        self.gas = None
        if g:
            if not isinstance(g, dict):
                raise ValueError("Relative permeability: 'gas' must be a dict of Corey gas-oil parameters.")
            gg = {k: _num(g, k, v, lo=0.0) for k, v in _GAS_DEFAULT.items()}
            for k in ('sgc', 'sorg'):
                if gg[k] >= 1:
                    raise ValueError(f"Relative permeability: gas '{k}' must be < 1.")
            if gg['krg_max'] > 1 or gg['krg_max'] <= 0:
                raise ValueError("Relative permeability: gas 'krg_max' must be in (0, 1].")
            if self.swc + gg['sgc'] + gg['sorg'] >= 0.999:
                raise ValueError("Relative permeability: swc + sgc + sorg must be < 1 for the gas-oil curves.")
            self.gas = gg

    # ---- construction -----------------------------------------------------------------
    @classmethod
    def from_params(cls, params: dict) -> 'RelPerm':
        return cls(params)

    def _init_table(self, rows):
        if not rows or len(rows) < 3:
            raise ValueError("Tabulated relative permeability needs at least 3 rows of (sw, krw, kro).")
        try:
            sw = np.array([float(r['sw']) for r in rows]); krw = np.array([float(r['krw']) for r in rows]); kro = np.array([float(r['kro']) for r in rows])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Tabulated relative permeability rows must have numeric 'sw', 'krw', 'kro' ({e}).")
        if not (np.all(np.isfinite(sw)) and np.all(np.isfinite(krw)) and np.all(np.isfinite(kro))):
            raise ValueError("Relative permeability table contains blank or non-numeric values.")
        if np.any(np.diff(sw) <= 0):
            raise ValueError("Relative permeability table: Sw must be strictly increasing.")
        if sw[0] < 0 or sw[-1] > 1:
            raise ValueError("Relative permeability table: Sw must lie within [0, 1].")
        if np.any(krw < 0) or np.any(krw > 1) or np.any(kro < 0) or np.any(kro > 1):
            raise ValueError("Relative permeability table: krw and kro must lie within [0, 1].")
        if np.any(np.diff(krw) < -1e-9):
            raise ValueError("Relative permeability table: krw must be non-decreasing with Sw (monotonicity violated).")
        if np.any(np.diff(kro) > 1e-9):
            raise ValueError("Relative permeability table: kro must be non-increasing with Sw (monotonicity violated).")
        if krw[0] > 1e-3:
            raise ValueError(f"Relative permeability table: krw at the first Sw ({sw[0]:g}) is {krw[0]:g}; the first row must be the connate-water end point with krw = 0.")
        if kro[0] <= 0:
            raise ValueError("Relative permeability table: kro at connate water (first row) must be > 0.")
        if krw[-1] <= 0:
            raise ValueError("Relative permeability table: krw at the last row must be > 0.")
        if kro[-1] > 1e-3:
            raise ValueError(f"Relative permeability table: kro at the last Sw ({sw[-1]:g}) is {kro[-1]:g}; the last row must be the residual-oil end point with kro = 0.")
        from scipy.interpolate import PchipInterpolator
        self.table_sw, self.table_krw, self.table_kro = sw, krw, kro
        self._pw = PchipInterpolator(sw, krw, extrapolate=False); self._po = PchipInterpolator(sw, kro, extrapolate=False)
        self.swc = float(sw[-1] if not np.any(krw <= 1e-9) else sw[np.where(krw <= 1e-9)[0][-1]])
        z = np.where(kro <= 1e-9)[0]
        self.sorw = float(1.0 - (sw[z[0]] if len(z) else sw[-1]))
        self.krw_max = float(np.interp(1.0 - self.sorw, sw, krw)); self.kro_max = float(kro[0])
        self.nw = self.no = None

    # ---- oil-water curves ---------------------------------------------------------------
    def _swn(self, sw):
        return _clip01((np.asarray(sw, dtype=float) - self.swc) / (1.0 - self.swc - self.sorw))

    def krw(self, sw):
        sw_a = np.asarray(sw, dtype=float)
        if self.model == 'table':
            out = np.where(sw_a <= self.table_sw[0], self.table_krw[0], np.where(sw_a >= self.table_sw[-1], self.table_krw[-1], np.nan_to_num(self._pw(sw_a))))
            return float(out) if out.ndim == 0 else out
        s = self._swn(sw_a)
        if self.model == 'corey':
            out = self.krw_max * s ** self.nw
        else:
            L, E, T = (self.let[k] for k in ('Lw', 'Ew', 'Tw'))
            out = self.krw_max * s ** L / (s ** L + E * (1 - s) ** T + 1e-300)
        return float(out) if np.ndim(out) == 0 else out

    def kro(self, sw):
        sw_a = np.asarray(sw, dtype=float)
        if self.model == 'table':
            out = np.where(sw_a <= self.table_sw[0], self.table_kro[0], np.where(sw_a >= self.table_sw[-1], self.table_kro[-1], np.nan_to_num(self._po(sw_a))))
            return float(out) if out.ndim == 0 else out
        s = self._swn(sw_a)
        if self.model == 'corey':
            out = self.kro_max * (1 - s) ** self.no
        else:
            L, E, T = (self.let[k] for k in ('Lo', 'Eo', 'To'))
            out = self.kro_max * (1 - s) ** L / ((1 - s) ** L + E * s ** T + 1e-300)
        return float(out) if np.ndim(out) == 0 else out

    # ---- gas-oil (Corey) ----------------------------------------------------------------
    def _sgn(self, sg):
        g = self.gas
        return _clip01((np.asarray(sg, dtype=float) - g['sgc']) / (1.0 - self.swc - g['sgc'] - g['sorg']))

    def krg(self, sg):
        if self.gas is None:
            raise ValueError("No gas-oil curves defined: add a 'gas' dict (sgc, sorg, krg_max, ng, nog) to the relperm parameters.")
        out = self.gas['krg_max'] * self._sgn(sg) ** self.gas['ng']
        return float(out) if np.ndim(out) == 0 else out

    def krog(self, sg):
        if self.gas is None:
            raise ValueError("No gas-oil curves defined: add a 'gas' dict (sgc, sorg, krg_max, ng, nog) to the relperm parameters.")
        out = self.kro_max * (1 - self._sgn(sg)) ** self.gas['nog']
        return float(out) if np.ndim(out) == 0 else out

    # ---- fractional flow ----------------------------------------------------------------
    def fractional_flow_w(self, sw, mu_w=None, mu_o=None, bw=1.0, bo=1.0, surface=False):
        """Water fractional flow fw = 1 / (1 + (kro/krw)(mu_w/mu_o)) at reservoir conditions.

        ``surface=True`` returns the surface (stock-tank) water cut instead, converting with bw, bo.
        """
        mu_w = self.mu_w if mu_w is None else mu_w; mu_o = self.mu_o if mu_o is None else mu_o
        krw = np.asarray(self.krw(sw), dtype=float); kro = np.asarray(self.kro(sw), dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            fw = np.where(krw <= 0, 0.0, np.where(kro <= 0, 1.0, 1.0 / (1.0 + (kro / np.where(krw > 0, krw, 1.0)) * (mu_w / mu_o))))
        if surface:
            fw = fw / bw / np.where(fw / bw + (1 - fw) / bo > 0, fw / bw + (1 - fw) / bo, 1.0)
        return float(fw) if fw.ndim == 0 else fw

    def water_cut_surface(self, sw, mu_w=None, mu_o=None, bo=1.0, bw=1.0):
        """Stock-tank water cut qw/(qw+qo) from the reservoir fractional flow."""
        return self.fractional_flow_w(sw, mu_w, mu_o, bw=bw, bo=bo, surface=True)

    @property
    def mobility_ratio(self) -> float:
        """End-point mobility ratio M = (krw_max/mu_w) / (kro_max/mu_o)."""
        return (self.krw_max / self.mu_w) / (self.kro_max / self.mu_o)

    def curves(self, n: int = 60) -> pd.DataFrame:
        """Curves over [swc, 1 - sorw] widened to [0, 1] for plotting: Sw, krw, kro, fw (+ gas columns)."""
        sw = np.linspace(0.0, 1.0, max(int(n), 5))
        sw = np.unique(np.concatenate([sw, [self.swc, 1 - self.sorw]]))
        df = pd.DataFrame({'Sw': sw, 'krw': self.krw(sw), 'kro': self.kro(sw), 'fw': self.fractional_flow_w(sw)})
        if self.gas is not None:
            sg = np.linspace(0.0, 1 - self.swc, max(int(n), 5))
            df_g = pd.DataFrame({'Sg': sg, 'krg': self.krg(sg), 'krog': self.krog(sg)})
            df = pd.concat([df, df_g], axis=1)
        return df

    def summary(self) -> dict:
        s = {'model': self.model, 'swc': self.swc, 'sorw': self.sorw, 'krw_max': self.krw_max, 'kro_max': self.kro_max,
             'mu_o_cp': self.mu_o, 'mu_w_cp': self.mu_w, 'mobility_ratio': self.mobility_ratio,
             'mobile_range': 1 - self.swc - self.sorw, 'has_gas_oil': self.gas is not None}
        if self.model == 'corey':
            s.update({'nw': self.nw, 'no': self.no})
        elif self.model == 'let':
            s.update(self.let)
        else:
            s['table_rows'] = int(len(self.table_sw))
        try:
            f = bl_front(self); s.update({'front_sw': f['sw_front'], 'pvi_breakthrough': f['pvi_bt']})
        except Exception:
            pass
        return s


# ---- Buckley-Leverett (1-D frontal advance, no capillary pressure / gravity) -------------------
def bl_front(relperm: RelPerm, mu_w=None, mu_o=None, swi=None, n=4001) -> dict:
    """Welge tangent construction from (swi, 0) to the fractional-flow curve.

    Returns sw_front, fw_front, dfw_dsw (tangent slope), sw_avg_bt (average saturation behind the front
    at breakthrough = swi + 1/slope) and pvi_bt (pore volumes injected at breakthrough = 1/slope).
    For piston-like displacement (straight-line fw tie) the largest saturation is chosen.
    """
    swi = relperm.swc if swi is None else float(swi)
    swmax = 1.0 - relperm.sorw
    if swi >= swmax:
        raise ValueError("Initial water saturation must be below 1 - sorw for a Buckley-Leverett displacement.")
    sw = np.linspace(swi, swmax, n)[1:]
    fw = np.asarray(relperm.fractional_flow_w(sw, mu_w, mu_o), dtype=float)
    ratio = fw / (sw - swi)
    best = ratio.max(); idx = int(np.where(ratio >= best * (1 - 1e-9))[0][-1])
    slope = float(best)
    return {'sw_front': float(sw[idx]), 'fw_front': float(fw[idx]), 'dfw_dsw': slope,
            'sw_avg_bt': float(swi + 1.0 / slope), 'pvi_bt': float(1.0 / slope), 'swi': float(swi)}


def recovery_vs_pvi(relperm: RelPerm, mu_w=None, mu_o=None, bo=1.0, bw=1.0, swi=None, pvi_max=3.0, n=200) -> pd.DataFrame:
    """Analytical Buckley-Leverett recovery curve (Welge), vs pore volumes of water injected.

    Columns: PVI, Sw_avg, Recovery [fraction of initial oil in place], Np [PV], WC_res (reservoir fw at the
    producer), WC_surface (stock-tank water cut using bo, bw).  Before breakthrough recovery = PVI and the
    water cut is zero; afterwards Sw_avg = Sw2 + (1 - fw2) * PVI with Sw2 from 1/PVI = dfw/dSw (Welge).
    """
    f = bl_front(relperm, mu_w, mu_o, swi); swi = f['swi']; swmax = 1.0 - relperm.sorw
    pvi = np.linspace(0.0, float(pvi_max), int(n))
    if swmax - f['sw_front'] < 1e-6:   # piston-like displacement: all mobile oil is swept at breakthrough
        pvi = np.linspace(0.0, float(pvi_max), int(n)); pre = pvi <= f['pvi_bt']
        sw_avg = np.where(pre, swi + pvi, swmax); wc_res = np.where(pre, 0.0, 1.0)
        wc_surf = wc_res / bw / np.where(wc_res / bw + (1 - wc_res) / bo > 0, wc_res / bw + (1 - wc_res) / bo, 1.0)
        return pd.DataFrame({'PVI': pvi, 'Sw_avg': sw_avg, 'Np [PV]': sw_avg - swi, 'Recovery': (sw_avg - swi) / (1.0 - swi),
                             'WC_res': wc_res, 'WC_surface': wc_surf})
    sw2g = np.linspace(f['sw_front'], swmax, 6001)
    fwg = np.asarray(relperm.fractional_flow_w(sw2g, mu_w, mu_o), dtype=float)
    dfw = np.gradient(fwg, sw2g)
    dfw[0] = f['dfw_dsw']
    dfw = np.minimum.accumulate(np.maximum(dfw, 1e-12))
    q_of = 1.0 / dfw                                   # PVI at which saturation sw2g reaches the producer
    avg_g = sw2g + (1 - fwg) * q_of
    out = {'PVI': pvi, 'Sw_avg': np.zeros_like(pvi), 'WC_res': np.zeros_like(pvi)}
    pre = pvi <= f['pvi_bt']
    out['Sw_avg'][pre] = swi + pvi[pre]
    post = ~pre
    if post.any():
        qp = pvi[post]
        sw2 = np.interp(qp, q_of, sw2g, right=swmax)
        fw2 = np.interp(qp, q_of, fwg, right=1.0)
        sav = np.interp(qp, q_of, avg_g, right=swmax)
        out['Sw_avg'][post] = np.minimum(sav, swmax); out['WC_res'][post] = fw2
    sw_avg = out['Sw_avg']
    np_pv = sw_avg - swi
    wc_res = out['WC_res']
    wc_surf = wc_res / bw / np.where(wc_res / bw + (1 - wc_res) / bo > 0, wc_res / bw + (1 - wc_res) / bo, 1.0)
    return pd.DataFrame({'PVI': pvi, 'Sw_avg': sw_avg, 'Np [PV]': np_pv, 'Recovery': np_pv / (1.0 - swi),
                         'WC_res': wc_res, 'WC_surface': wc_surf})
