"""One place every tab gets its data from: the data hub for the model on screen, plus the shared export basket.

``current_hub`` rebuilds the hub only when the model, the solve or the forecast changed, so all tabs see the same tables.
``table_actions`` puts a "send to export basket" checkbox and CSV / Excel buttons under any table, so a table seen on any tab can be exported
or post-processed without re-computing it."""
from __future__ import annotations
import io, copy
import pandas as pd
from network import data_hub as dh

KEY = 'hub_cache'; BASKET = 'export_basket'; EXTRA = 'pp_tables'


def _sig(ss, mh):
    fc = ss.get('forecast'); r = ss.get('solve')
    return (mh, ss.get('forecast_hash'), id(fc) if fc else None, id((ss.get('case_forecast') or {}).get('fc')), id(r) if r else None, len(ss.get(EXTRA) or {}), tuple(sorted((ss.get(EXTRA) or {}).keys())))


def current_hub(st, nodes, edges, solved):
    """Hub for the model on screen. Stale solves / forecasts are not used (they would break consistency) - the hub says what it left out."""
    from ui.graph_contract import graph_hash
    ss = st.session_state; mh = graph_hash(nodes, edges); sig = _sig(ss, mh)
    c = ss.get(KEY)
    if c and c['sig'] == sig: return c['hub']
    fc = ss.get('forecast') if ss.get('forecast_hash') == mh else None
    cf = ss.get('case_forecast')                                    # forecast stored with a loaded case (valid while the model is unchanged)
    if fc is None and cf and cf.get('hash') == mh: fc = cf['fc']
    sol = solved()
    hub = dh.build_hub(nodes, edges, sol, fc, model_hash=mh, forecast_hash=mh if fc else None, solve_hash=mh if sol else None)
    hub.info['stale_forecast'] = bool(ss.get('forecast') and not fc); hub.forecast = fc
    for k, df in (ss.get(EXTRA) or {}).items(): hub.add(k, df, 'Post-processed table (Python)', 'post-process')
    ss[KEY] = {'sig': sig, 'hub': hub}
    return hub


def basket(st): return st.session_state.setdefault(BASKET, [])


def add_to_basket(st, name):
    b = basket(st)
    if name not in b: b.append(name)


def xlsx_bytes(df, sheet='data'):
    buf = io.BytesIO()
    for eng in ('openpyxl', 'xlsxwriter'):
        try:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine=eng) as xw: df.to_excel(xw, sheet_name=sheet[:31], index=False)
            return buf.getvalue()
        except ImportError: continue
    return None   # no Excel writer installed: the Excel button is disabled, CSV still works


def table_actions(st, df, name, key, description=''):
    """Downloads + export-basket toggle under a table. Registers the table as a post-process/export dataset when ticked."""
    if df is None or len(df) == 0: return
    ss = st.session_state; reg = ss.setdefault('shared_tables', {}); c1, c2, c3 = st.columns([2, 1, 1])
    on = c1.checkbox(f'Add "{name}" to the export basket', value=name in reg, key=f'ta_{key}', help='Collected in Cases & Data > Export. The same table is then available to the Python post-processing.')
    if on: reg[name] = df.copy()
    else: reg.pop(name, None)
    c2.download_button('CSV', df.to_csv(index=False), f'{name}.csv', 'text/csv', key=f'ta_csv_{key}', use_container_width=True)
    xb = xlsx_bytes(df, name)
    if xb is None: c3.button('Excel (not installed)', key=f'ta_xl_{key}', disabled=True, use_container_width=True, help='Add openpyxl to requirements.txt')
    else: c3.download_button('Excel', xb, f'{name}.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', key=f'ta_xl_{key}', use_container_width=True)


def shared_tables(st): return dict(st.session_state.get('shared_tables') or {})
