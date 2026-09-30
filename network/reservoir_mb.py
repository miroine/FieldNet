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

Screening/planning model — not a substitute for a reservoir simulator.
Canonical units: bar, Sm3, rm3, °C, days.
"""
from __future__ import annotations
import copy, math
from physics.pvt import simple_black_oil

PHASES = ('oil', 'gas', 'gas_condensate')
T_SC_K = 288.15; P_SC_BAR = 1.01325

TANK_DEFAULTS = {
    'fluid_phase': 'oil', 'reservoir_pressure_bar': 250.0, 'temperature_c': 90.0,
    'stoiip_sm3': 20e6, 'giip_sm3': 5e9, 'boi_rm3_sm3': 1.25, 'rsi_sm3_sm3': 100.0,
    'bubble_point_bar': 150.0, 'swi': 0.2, 'ct_1bar': 1.5e-4, 'gas_sg': 0.7,
    'cgr_sm3_per_msm3': 100.0, 'aquifer_pi_m3d_bar': 0.0, 'min_pressure_bar': 20.0,
    # screening fluid-evolution model for oil tanks
    'water_breakthrough_rf': 0.05, 'max_water_cut': 0.9, 'rf_at_max_water_cut': 0.40, 'gor_rise_factor': 3.0,
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
    def __init__(self, node):
        p = node.get('params', {}) or {}
        self.id = node['id']; self.name = node.get('name', node['id'])
        ph = str(p.get('fluid_phase', 'oil')).lower()
        self.phase = ph if ph in PHASES else 'oil'
        self.pi = max(_f(p, 'reservoir_pressure_bar'), 1.0); self.p = self.pi
        self.t = _f(p, 'temperature_c'); self.swi = min(max(_f(p, 'swi'), 0.0), 0.9)
        self.pmin = _f(p, 'min_pressure_bar'); self.gas_sg = _f(p, 'gas_sg')
        self.jaq = max(_f(p, 'aquifer_pi_m3d_bar'), 0.0)
        self.np = self.gp = self.wp = self.winj = self.ginj = self.we = 0.0
        if self.phase == 'oil':
            self.n = max(_f(p, 'stoiip_sm3'), 1.0); self.boi = max(_f(p, 'boi_rm3_sm3'), 1.0)
            self.rsi = max(_f(p, 'rsi_sm3_sm3'), 0.0); self.pb = min(_f(p, 'bubble_point_bar'), self.pi)
            self.ct = max(_f(p, 'ct_1bar'), 1e-7)
            self.pv = self.n * self.boi / (1 - self.swi)
            self.g = self.n * self.rsi
            self.rf_bt = max(_f(p, 'water_breakthrough_rf'), 0.0); self.wc_max = min(max(_f(p, 'max_water_cut'), 0.0), 0.99)
            self.rf_wcmax = max(_f(p, 'rf_at_max_water_cut'), self.rf_bt + 1e-3); self.gor_rise = max(_f(p, 'gor_rise_factor'), 0.0)
        else:
            self.g = max(_f(p, 'giip_sm3'), 1.0); self.bgi = bg_rm3_sm3(self.pi, self.t, self.gas_sg)
            self.pv = self.g * self.bgi / (1 - self.swi)
            self.cgr = max(_f(p, 'cgr_sm3_per_msm3'), 0.0) if self.phase == 'gas_condensate' else 0.0
            self.n = self.g * self.cgr / 1e6

    # ---- fluid the tank delivers to its wells --------------------------------------
    def well_overrides(self, well_params=None):
        o = {'reservoir_pressure_bar': self.p}
        wp = well_params or {}
        if self.phase == 'oil':
            # Water cut rises from the well's initial value after breakthrough (smooth S-curve in
            # recovery factor); GOR rises once the tank drops below the bubble point.
            rf = self.np / self.n; wc0 = float(wp.get('initial_water_cut', wp.get('water_cut', 0.0)) or 0.0)
            x = min(max((rf - self.rf_bt) / (self.rf_wcmax - self.rf_bt), 0.0), 1.0); sx = x * x * (3 - 2 * x)
            o['water_cut'] = max(wc0, wc0 + (self.wc_max - wc0) * sx); o['initial_water_cut'] = wc0
            gor0 = float(wp.get('initial_gor_sm3sm3', wp.get('gor_sm3sm3', self.rsi)) or self.rsi); o['initial_gor_sm3sm3'] = gor0
            o['gor_sm3sm3'] = gor0 * (1 + self.gor_rise * max(self.pb - self.p, 0.0) / max(self.pb, 1.0))
        if self.phase != 'oil':
            cgr = max(self.cgr, 1.0) if self.phase == 'gas_condensate' else 2.0  # dry gas: small liquid yield
            o['gor_sm3sm3'] = 1e6 / cgr
            o['ipr_model'] = 'Gas'  # gas wells use backpressure deliverability, not a liquid PI
            # Beggs-Brill grossly over-predicts holdup at gas-well liquid fractions (~1e-4);
            # the no-slip model is the usual screening choice for gas/condensate tubing.
            o['vlp_model'] = o['correlation'] = 'Homogeneous'
        return o

    # ---- material balance -----------------------------------------------------------
    def _ct_eff(self, p):
        if self.phase != 'oil' or p >= self.pb: return self.ct
        so = 1 - self.swi
        return self.ct + so * bg_rm3_sm3(p, self.t, self.gas_sg) * (self.rsi / max(self.pb, 1.0)) / self.boi

    def step(self, oil_sm3, water_m3, gas_sm3, water_inj_m3=0.0, gas_inj_sm3=0.0, dt_days=0.0):
        self.np += oil_sm3; self.wp += water_m3; self.gp += gas_sm3
        self.winj += water_inj_m3; self.ginj += gas_inj_sm3
        if self.phase == 'oil':
            sub = 10; void = oil_sm3 * self.boi + water_m3 - water_inj_m3
            for _ in range(sub):
                we = self.jaq * max(self.pi - self.p, 0.0) * dt_days / sub; self.we += we
                dp = (void / sub - we) / (self.pv * self._ct_eff(self.p))
                self.p = max(self.pmin, min(self.pi, self.p - dp))
        else:
            # G Bgi = (G - Gp + Ginj) Bg + We + Winj - Wp   ->  solve Bg, then p
            for _ in range(10):
                self.we += self.jaq * max(self.pi - self.p, 0.0) * dt_days / 10
            net_g = self.g - self.gp + self.ginj
            if net_g <= 0: self.p = self.pmin; return self.p
            target_bg = (self.g * self.bgi - (self.we + self.winj - self.wp)) / net_g
            lo, hi = 0.5, self.pi * 1.5
            for _ in range(60):
                m = 0.5 * (lo + hi)
                if bg_rm3_sm3(m, self.t, self.gas_sg) > target_bg: lo = m
                else: hi = m
            self.p = max(self.pmin, min(0.5 * (lo + hi), self.pi * 1.5))
        return self.p

    def rf(self):
        if self.phase == 'oil': return {'oil': self.np / self.n, 'gas': self.gp / max(self.g, 1.0)}
        return {'gas': self.gp / self.g, 'oil': self.np / max(self.n, 1.0) if self.n > 0 else 0.0}

    def row(self):
        r = self.rf()
        return {'Tank': self.name, 'Tank ID': self.id, 'Phase': self.phase, 'Pressure [bar]': self.p,
                'Cum oil [Sm3]': self.np, 'Cum gas [Sm3]': self.gp, 'Cum water [m3]': self.wp,
                'Cum water inj [m3]': self.winj, 'Aquifer influx [m3]': self.we,
                'RF oil [%]': 100 * r['oil'], 'RF gas [%]': 100 * r['gas']}


def tanks_from_nodes(nodes):
    return {n['id']: Tank(n) for n in nodes if n.get('kind') == 'reservoir'}


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
