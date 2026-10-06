"""Reservoir tanks defined by in-place volumes and fluid phase (material balance).

A ``reservoir`` node on the canvas is a *tank*, not a pipe boundary. Wells (and injectors)
are assigned to a tank with ``params.reservoir_id`` (drag tank → well in the editor). At
every solve/forecast step the tank pressure becomes the well's reservoir pressure; the
tank is then depleted from the produced (and injected) volumes.

Supported phases
  * ``oil``           STOIIP [Sm3]; undersaturated compressibility above Pb and a
                      solution-gas-drive effective compressibility below Pb.
  * ``gas``           GIIP [Sm3]; p/z material balance (dry gas).
  * ``gas_condensate``GIIP [Sm3] + CGR; p/z material balance on the gas, wells produce
                      condensate at the tank CGR.
Optional steady-state (Schilthuis) aquifer: We' = J_aq (p_i - p).

v31: Supports real PVT tables (lab data, correlations) instead of fixed screening model.

Screening/planning model — not a substitute for a reservoir simulator.
Canonical units: bar, Sm3, rm3, °C, days.
"""
from __future__ import annotations
import copy, math
from typing import Optional, Dict
from physics.pvt import simple_black_oil
from physics.pvt_table import PVTTable

PHASES = ('oil', 'gas', 'gas_condensate')
T_SC_K = 288.15; P_SC_BAR = 1.01325

TANK_DEFAULTS = {
    'fluid_phase': 'oil', 'reservoir_pressure_bar': 250.0, 'temperature_c': 90.0,
    'stoiip_sm3': 20e6, 'giip_sm3': 5e9, 'boi_rm3_sm3': 1.25, 'rsi_sm3_sm3': 100.0,
    'bubble_point_bar': 150.0, 'swi': 0.2, 'ct_1bar': 1.5e-4, 'gas_sg': 0.7,
    'cgr_sm3_per_msm3': 100.0, 'aquifer_pi_m3d_bar': 0.0, 'min_pressure_bar': 20.0,
    # screening fluid-evolution model for oil tanks
    'water_breakthrough_rf': 0.05, 'max_water_cut': 0.9, 'rf_at_max_water_cut': 0.40, 'gor_rise_factor': 3.0,
    # relative-permeability driven fluid evolution (only used when params['relperm'] is set)
    'sweep_efficiency': 1.0,
}


def _f(p, k):
    try:
        v = float(p.get(k, TANK_DEFAULTS[k]))
        return v if math.isfinite(v) else float(TANK_DEFAULTS[k])
    except (TypeError, ValueError):
        return float(TANK_DEFAULTS[k])


def z_factor(p_bar, t_c, gas_sg=0.7):
    """Papay-type z screening correlation (pseudo-critical from Sutton)."""
    ppc = (756.8 - 131.0 * gas_sg - 3.6 * gas_sg ** 2) * 0.0689476  # psia -> bar
    tpc = (169.2 + 349.5 * gas_sg - 74.0 * gas_sg ** 2) / 1.8        # R -> K
    ppr = max(p_bar, 0.01) / ppc; tpr = (t_c + 273.15) / tpc
    z = 1 - 3.53 * ppr / (10 ** (0.9813 * tpr)) + 0.274 * ppr ** 2 / (10 ** (0.8157 * tpr))
    return min(max(z, 0.3), 1.5)


def bg_rm3_sm3(p_bar, t_c, gas_sg=0.7):
    return z_factor(p_bar, t_c, gas_sg) * (t_c + 273.15) / T_SC_K * P_SC_BAR / max(p_bar, 0.01)


class Tank:
    def __init__(self, node, pvt_table: Optional[PVTTable] = None):
        """Initialize tank with optional real PVT table (v31).

        Args:
            node: Graph node dict
            pvt_table: PVTTable instance (real lab data). If None, uses screening model.
        """
        p = node.get('params', {}) or {}
        self.id = node['id']; self.name = node.get('name', node['id'])
        ph = str(p.get('fluid_phase', 'oil')).lower()
        self.phase = ph if ph in PHASES else 'oil'
        self.pi = max(_f(p, 'reservoir_pressure_bar'), 1.0); self.p = self.pi
        self.t = _f(p, 'temperature_c'); self.swi = min(max(_f(p, 'swi'), 0.0), 0.9)
        self.pmin = _f(p, 'min_pressure_bar'); self.gas_sg = _f(p, 'gas_sg')
        self.jaq = max(_f(p, 'aquifer_pi_m3d_bar'), 0.0)
        self.np = self.gp = self.wp = self.winj = self.ginj = self.we = 0.0
        # Communication with other tanks (canvas: tank -> tank link). xin = cumulative net reservoir volume received [m3].
        self.xin = 0.0
        self.comm = [c for c in (p.get('communication') or []) if isinstance(c, dict) and c.get('to')]
        # Prediction mode (GAP style): 'material_balance' (default) or 'external' = pressure (and optional water cut / GOR) follow a table
        # exported from a reservoir simulator instead of the tank balance. The balance still tracks cumulatives and recovery factor.
        self.mode = str(p.get('prediction_mode', 'material_balance') or 'material_balance').lower()
        self.ext = list(p.get('external_table') or []) if self.mode == 'external' else []
        self.ext_wc = self.ext_gor = None
        # Optional relative-permeability model (oil tanks only). ``sw_avg`` is the tank-average water saturation.
        self.rp = None; self.sw_avg = self.swi
        self.sweep = min(max(_f(p, 'sweep_efficiency'), 0.05), 1.0)
        self.water_cut_mode = str(p.get('water_cut_mode', 'auto') or 'auto').lower()
        if ph == 'oil' and isinstance(p.get('relperm'), dict) and p.get('relperm'):
            from physics.relperm import RelPerm
            self.rp = RelPerm.from_params(p['relperm'])
            self.sweep = min(max(float(p['relperm'].get('sweep_efficiency', self.sweep)), 0.05), 1.0)

        # v31: PVT table. A *real* table (lab data) drives Bo/Bg/Rs/Pb. Without one the tank uses the
        # fluid properties entered on the node (bubble_point_bar, boi_rm3_sm3, z-factor correlation);
        # the generic screening table is kept only so ``tank.pvt`` stays available to callers.
        # (T1.1 had routed every tank through the fixed Pb=150 bar screening table, which silently
        #  ignored the user's bubble point and Bo and broke the solution-gas-drive and p/z balances.)
        self._table = bool(pvt_table is not None and pvt_table.phase == self.phase)
        if self._table:
            self.pvt = pvt_table
            self.pvt_source = pvt_table.source
        else:
            self.pvt = PVTTable.screening_default(self.phase)
            self.pvt_source = 'Screening (node properties: Pb, Bo, z-factor correlation)'

        if self.phase == 'oil':
            self.n = max(_f(p, 'stoiip_sm3'), 1.0); self.boi = max(_f(p, 'boi_rm3_sm3'), 1.0)
            self.rsi = max(_f(p, 'rsi_sm3_sm3'), 0.0)
            # Pb comes from the PVT table when one is supplied, otherwise from the node
            self.pb = (self.pvt.pbub if (self._table and self.pvt.pbub) else min(_f(p, 'bubble_point_bar'), self.pi))
            self.rsb_table = self.pvt.rsb if self._table else self.rsi
            self.ct = max(_f(p, 'ct_1bar'), 1e-7)
            self.pv = self.n * self.boi / (1 - self.swi)
            self.g = self.n * self.rsi
            self.rf_bt = max(_f(p, 'water_breakthrough_rf'), 0.0); self.wc_max = min(max(_f(p, 'max_water_cut'), 0.0), 0.99)
            self.rf_wcmax = max(_f(p, 'rf_at_max_water_cut'), self.rf_bt + 1e-3); self.gor_rise = max(_f(p, 'gor_rise_factor'), 0.0)
        else:
            self.g = max(_f(p, 'giip_sm3'), 1.0); self.bgi = self._bg(self.pi)
            self.pv = self.g * self.bgi / (1 - self.swi)
            self.cgr = max(_f(p, 'cgr_sm3_per_msm3'), 0.0) if self.phase == 'gas_condensate' else 0.0
            self.n = self.g * self.cgr / 1e6

    # ---- external (simulator) prediction --------------------------------------------
    def apply_external(self, day, t0=None):
        """Set pressure (and optional water cut / GOR) from the external table at ``day`` days after the forecast start ``t0`` (ISO date / datetime).
        Rows: ``{'date' | 'time_days', 'reservoir_pressure_bar', 'water_cut'?, 'gor_sm3sm3'?}``. Linear interpolation, held flat outside the table.
        Returns True when the table was applied."""
        if self.mode != 'external' or not self.ext: return False
        import numpy as _np
        from datetime import datetime as _dt
        t0d = _dt.fromisoformat(str(t0)[:10]) if t0 is not None else None
        xs = []; rows = []
        for r in self.ext:
            try:
                if r.get('time_days') is not None and str(r.get('time_days')) != '': x = float(r['time_days'])
                elif r.get('date') and t0d is not None: x = (_dt.fromisoformat(str(r['date'])[:10]) - t0d).days
                else: continue
            except (TypeError, ValueError): continue
            xs.append(x); rows.append(r)
        if not xs: return False
        order = _np.argsort(xs); xs = _np.asarray(xs)[order]; rows = [rows[i] for i in order]
        def col(k):
            pts = [(x, float(r[k])) for x, r in zip(xs, rows) if r.get(k) is not None and str(r.get(k)) not in ('', 'nan')]
            if not pts: return None
            return float(_np.interp(float(day), [a for a, _ in pts], [b for _, b in pts]))
        pr = col('reservoir_pressure_bar')
        if pr is not None: self.p = max(pr, 1.0)
        self.ext_wc = col('water_cut'); self.ext_gor = col('gor_sm3sm3')
        return True

    # ---- fluid the tank delivers to its wells --------------------------------------
    def well_overrides(self, well_params=None):
        o = self._well_overrides_mb(well_params)
        if self.ext_wc is not None: o['water_cut'] = min(max(self.ext_wc, 0.0), 0.99)
        if self.ext_gor is not None and self.phase == 'oil': o['gor_sm3sm3'] = max(self.ext_gor, 0.0)
        return o

    def _well_overrides_mb(self, well_params=None):
        o = {'reservoir_pressure_bar': self.p}
        wp = well_params or {}
        if self.phase == 'oil':
            # Water cut rises from the well's initial value after breakthrough (smooth S-curve in
            # recovery factor); GOR rises once the tank drops below the bubble point (from PVT table).
            rf = self.np / self.n; wc0 = float(wp.get('initial_water_cut', wp.get('water_cut', 0.0)) or 0.0)
            x = min(max((rf - self.rf_bt) / (self.rf_wcmax - self.rf_bt), 0.0), 1.0); sx = x * x * (3 - 2 * x)
            o['water_cut'] = max(wc0, wc0 + (self.wc_max - wc0) * sx); o['initial_water_cut'] = wc0
            if self._use_relperm_wc():  # relperm fractional flow at the tank-average Sw replaces the S-curve
                o['water_cut'] = max(wc0, min(self._relperm_wc(), 0.99))
            gor0 = float(wp.get('initial_gor_sm3sm3', wp.get('gor_sm3sm3', self.rsi)) or self.rsi); o['initial_gor_sm3sm3'] = gor0
            if self._table:  # GOR from the supplied PVT table
                o['gor_sm3sm3'] = max(gor0, self.pvt.get_gor(self.p))
            else:            # screening rise below the bubble point
                o['gor_sm3sm3'] = gor0 * (1 + self.gor_rise * max(self.pb - self.p, 0.0) / max(self.pb, 1.0))
            if self.rp is not None and self.rp.gas is not None and self.p < self.pb:
                o['gor_sm3sm3'] = max(o['gor_sm3sm3'], self._relperm_gor())
        if self.phase != 'oil':
            cgr = max(self.cgr, 1.0) if self.phase == 'gas_condensate' else 2.0  # dry gas: small liquid yield
            o['gor_sm3sm3'] = 1e6 / cgr
            o['ipr_model'] = 'Gas'  # gas wells use backpressure deliverability, not a liquid PI
            # Beggs-Brill grossly over-predicts holdup at gas-well liquid fractions (~1e-4);
            # the no-slip model is the usual screening choice for gas/condensate tubing.
            o['vlp_model'] = o['correlation'] = 'Homogeneous'
        return o

    # ---- relative-permeability fluid response (screening, average-saturation model) ----------------
    def _use_relperm_wc(self):
        return self.rp is not None and self.water_cut_mode != 'screening'

    def _update_sw_avg(self):
        """Average water saturation of the tank.

        Voidage balance on the swept pore volume: net water retained in the pore space
        (aquifer influx + injection - water produced, rm3, Bw = 1, water compressibility ignored) displaces oil
        from the swept part ``E * PV``; the retained water saturation increases by net/(E*PV) above Swi.
        Sw_avg = Swi + max(We + Winj - Wp, 0) / (E * PV), capped at 1 - Sorw. Water is assumed to be evenly
        distributed over the swept volume (no front, no gravity, no capillary pressure): a tank/screening
        simplification that gives earlier and smoother water breakthrough than a frontal-advance model."""
        net = max(self.we + self.winj - self.wp, 0.0)
        self.sw_avg = min(self.swi + net / (self.sweep * self.pv), max(1.0 - self.rp.sorw, self.swi))

    def _relperm_wc(self):
        return float(self.rp.water_cut_surface(self.sw_avg, bo=self._bo(self.p)))

    def _solution_rs(self, p):
        if self._table: return self.pvt.get_gor(p)
        return self.rsi * min(p / max(self.pb, 1.0), 1.0)

    def _relperm_gor(self):
        """Producing GOR = Rs + (krg/mu_g)/(kro/mu_o) * Bo/Bg once free gas exceeds the critical saturation.
        Free gas from the solution-gas balance (no gas cap, no segregation)."""
        p = self.p
        free = self.g - self.gp - (self.n - self.np) * self._solution_rs(p) + self.ginj  # Sm3
        sg = min(max(free * self._bg(p) / self.pv, 0.0), max(1.0 - self.sw_avg - self.rp.gas['sorg'], 0.0))
        rs = self._solution_rs(p)
        krg = self.rp.krg(sg); kro = max(self.rp.krog(sg), 1e-6)
        if krg <= 0: return rs
        return rs + (krg / self.rp.mu_g) / (kro / self.rp.mu_o) * self._bo(p) / max(self._bg(p), 1e-9)

    # ---- fluid properties: real table if supplied, node properties otherwise ------------
    def _bg(self, p):
        return self.pvt.get_bg(p, self.t) if self._table else bg_rm3_sm3(p, self.t, self.gas_sg)

    def _bo(self, p):
        return self.pvt.get_bo(p, self.t) if self._table else self.boi

    # ---- material balance -----------------------------------------------------------
    def _ct_eff(self, p):
        """Effective compressibility (solution-gas expansion below the bubble point)."""
        if self.phase != 'oil' or p >= self.pb: return self.ct
        so = 1 - self.swi
        if self._table:
            return self.ct + so * self.pvt.get_bg(p, self.t) * (self.pvt.get_gor(p) / max(self.pb, 1.0)) / self.pvt.get_bo(p, self.t)
        return self.ct + so * bg_rm3_sm3(p, self.t, self.gas_sg) * (self.rsi / max(self.pb, 1.0)) / self.boi

    def step(self, oil_sm3, water_m3, gas_sm3, water_inj_m3=0.0, gas_inj_sm3=0.0, dt_days=0.0):
        """Material balance step using real PVT table."""
        self.np += oil_sm3; self.wp += water_m3; self.gp += gas_sm3
        self.winj += water_inj_m3; self.ginj += gas_inj_sm3
        if self.phase == 'oil':
            sub = 10; void = oil_sm3 * self._bo(self.p) + water_m3 - water_inj_m3
            for _ in range(sub):
                we = self.jaq * max(self.pi - self.p, 0.0) * dt_days / sub; self.we += we
                dp = (void / sub - we) / (self.pv * self._ct_eff(self.p))
                self.p = max(self.pmin, min(self.pi, self.p - dp))
            if self.rp is not None: self._update_sw_avg()
        else:
            # G Bgi = (G - Gp + Ginj) Bg + We + Winj - Wp   ->  solve Bg, then p (from PVT table)
            for _ in range(10):
                self.we += self.jaq * max(self.pi - self.p, 0.0) * dt_days / 10
            net_g = self.g - self.gp + self.ginj
            if net_g <= 0: self.p = self.pmin; return self.p
            target_bg = (self.g * self.bgi - (self.we + self.winj - self.wp + self.xin)) / net_g
            lo, hi = 0.5, self.pi * 1.5
            for _ in range(60):
                m = 0.5 * (lo + hi)
                if self._bg(m) > target_bg: lo = m
                else: hi = m
            self.p = max(self.pmin, min(0.5 * (lo + hi), self.pi * 1.5))
        return self.p

    def compliance(self):
        """Reservoir volume per bar [m3/bar] (pore volume x effective compressibility)."""
        if self.phase == 'oil': return self.pv * self._ct_eff(self.p)
        dp = 1.0; b0 = self._bg(self.p); b1 = self._bg(max(self.p - dp, 1.0))
        return max(self.pv * (b1 - b0) / max(b0, 1e-9), 1e3)

    def exchange(self, rm3):
        """Receive (+) or give up (-) a reservoir volume through a communication link."""
        self.xin += rm3
        if self.phase == 'oil':
            self.p = max(self.pmin, min(self.pi * 1.5, self.p + rm3 / (self.pv * self._ct_eff(self.p))))
        else:
            net_g = self.g - self.gp + self.ginj
            if net_g > 0:
                target_bg = (self.g * self.bgi - (self.we + self.winj - self.wp + self.xin)) / net_g
                lo, hi = 0.5, self.pi * 1.5
                for _ in range(60):
                    m = 0.5 * (lo + hi)
                    if self._bg(m) > target_bg: lo = m
                    else: hi = m
                self.p = max(self.pmin, min(0.5 * (lo + hi), self.pi * 1.5))
        return self.p

    def rf(self):
        if self.phase == 'oil': return {'oil': self.np / self.n, 'gas': self.gp / max(self.g, 1.0)}
        return {'gas': self.gp / self.g, 'oil': self.np / max(self.n, 1.0) if self.n > 0 else 0.0}

    def row(self):
        r = self.rf()
        out = self._row_base(r)
        if self.rp is not None: out['Sw avg [-]'] = self.sw_avg
        return out

    def _row_base(self, r):
        return {'Tank': self.name, 'Tank ID': self.id, 'Phase': self.phase, 'Pressure [bar]': self.p,
                'Cum oil [Sm3]': self.np, 'Cum gas [Sm3]': self.gp, 'Cum water [m3]': self.wp,
                'Cum water inj [m3]': self.winj, 'Aquifer influx [m3]': self.we, 'Net communication [m3]': self.xin,
                'RF oil [%]': 100 * r['oil'], 'RF gas [%]': 100 * r['gas']}


def communication_transfers(tanks, dt_days):
    """Net reservoir volume [m3] each tank receives from its communication links over ``dt_days``.

    Link flow = T (p_from - p_to) towards the lower pressure, limited by ``max_transfer_m3d`` and by 90 % of the
    volume that would equalise the two pressures (so a long step cannot overshoot). Links are symmetric: a link
    declared on either tank applies once to the pair."""
    net = {tid: 0.0 for tid in tanks}; seen = set()
    for tid, tk in tanks.items():
        for c in tk.comm:
            o = c.get('to')
            if o not in tanks or o == tid: continue
            key = tuple(sorted((tid, o)))
            if key in seen: continue
            seen.add(key); a, b = tanks[tid], tanks[o]
            T = max(float(c.get('transmissibility_m3d_bar') or 0.0), 0.0); mx = c.get('max_transfer_m3d'); mx = float('inf') if mx in (None, '') else float(mx)
            dp = a.p - b.p
            if T <= 0 or abs(dp) < 1e-9 or dt_days <= 0: continue
            vol = min(T * abs(dp) * dt_days, mx * dt_days, 0.9 * abs(dp) / (1.0 / a.compliance() + 1.0 / b.compliance()))
            if dp > 0: net[tid] -= vol; net[o] += vol
            else: net[tid] += vol; net[o] -= vol
    return net


def tanks_from_nodes(nodes, pvt_tables: Optional[Dict[str, PVTTable]] = None):
    """Create Tank instances from nodes.

    Args:
        nodes: List of graph nodes
        pvt_tables: Optional dict mapping tank ID → PVTTable

    Returns:
        Dict mapping tank ID → Tank instance
    """
    pvt_tables = pvt_tables or {}
    return {n['id']: Tank(n, pvt_table=pvt_tables.get(n['id']))
            for n in nodes if n.get('kind') == 'reservoir'}


def apply_tank_links(nodes, tanks=None):
    """Copy tank pressure (and, for gas tanks, the fluid) onto every linked well/injector.

    Returns a new node list; unlinked wells keep their own reservoir pressure.
    """
    ns = copy.deepcopy(nodes); tanks = tanks if tanks is not None else tanks_from_nodes(ns)
    for n in ns:
        rid = (n.get('params') or {}).get('reservoir_id')
        if rid and rid in tanks and n.get('kind') in ('well', 'water_injector', 'gas_injector', 'injector'):
            ov = tanks[rid].well_overrides(n.get('params'))
            if n['kind'] != 'well': ov = {'reservoir_pressure_bar': ov['reservoir_pressure_bar']}
            n.setdefault('params', {}).update(ov); n['params']['_tank_linked'] = True
    return ns


def ensure_tank_links(nodes):
    """Apply tank links unless the caller (e.g. the forecast, with *current* tank pressures)
    already did. Every solver entry point calls this, so no path can silently fall back to a
    well's own reservoir pressure."""
    if not any(n.get('kind') == 'reservoir' for n in nodes): return nodes
    if all((n.get('params') or {}).get('_tank_linked') for n in nodes
           if (n.get('params') or {}).get('reservoir_id') and n.get('kind') in ('well', 'water_injector', 'gas_injector', 'injector')):
        return nodes
    return apply_tank_links(nodes)


def tank_summary(nodes):
    """Static in-place summary for display."""
    rows = []
    for n in nodes:
        if n.get('kind') != 'reservoir': continue
        t = Tank(n); linked = [w.get('name', w['id']) for w in nodes if (w.get('params') or {}).get('reservoir_id') == n['id']]
        rows.append({'Tank': t.name, 'Phase': t.phase, 'Pi [bar]': t.pi, 'STOIIP [MSm3]': t.n / 1e6 if t.phase == 'oil' else None,
                     'GIIP [GSm3]': t.g / 1e9, 'Pore volume [MSm3 res]': t.pv / 1e6, 'Linked wells/injectors': ', '.join(linked) or '—'})
    return rows
