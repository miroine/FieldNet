"""Yearly bar charts (calendar-year volumes) for the field, wells, tanks and groups - the numbers come from the data hub."""
from __future__ import annotations
import pandas as pd
from network import annual
from ui.hub_access import table_actions

SOURCES = {'Field': ('annual_field', None), 'Wells': ('annual_wells', 'Well'), 'Tanks': ('annual_tanks', 'Tank'), 'Groups': ('groups_annual', 'Group')}
SKIP = ('Year', 'Days', 'Partial', 'Well', 'Well ID', 'Tank', 'Tank ID', 'Group')


def render_annual(st, hub, key='an'):
    st.subheader('Yearly profiles')
    st.caption('Calendar-year volumes integrated from the forecast steps (a step that crosses 1 January is split by days), so the bars add up exactly to the cumulative. '
               'Years the forecast does not fully cover are drawn lighter.')
    avail = {k: v for k, v in SOURCES.items() if v[0] in hub.datasets}
    if 'annual_field' not in hub.datasets:
        st.info('Run a forecast on the Development schedule tab first - the yearly bars are computed from it.' + (' (The forecast on screen is out of date for the current model.)' if hub.info.get('stale_forecast') else '')); return
    c1, c2, c3 = st.columns([1, 2, 2])
    src = c1.radio('Show', list(avail), key=f'{key}_src', horizontal=False)
    ds, ent = avail[src]; raw = hub.datasets[ds]
    unit = c2.selectbox('Units', list(annual.UNITS), key=f'{key}_unit')
    oe = c3.checkbox('Add oil equivalent (1 Sm³ oil = 1000 Sm³ gas)', value=False, key=f'{key}_oe'); stacked = c3.checkbox('Stack the bars', value=src != 'Field', key=f'{key}_stack')
    conv, labels = annual.convert(raw, unit, oe)
    measures = [c for c in conv.columns if c not in SKIP and not c.startswith('Cum ') and 'pressure' not in c.lower()]
    if not measures: st.info('Nothing to plot.'); return
    if ent is None:
        pick = st.multiselect('Series', measures, default=sorted([m for m in measures if m.startswith(('Oil [', 'Gas [', 'Water ['))], key=lambda m: ((0 if m.startswith('Gas') else 1) if st.session_state.get('_phase_resolved') == 'Gas' else (0 if m.startswith('Oil') else 1)))[:3], key=f'{key}_series')
        cumul = st.checkbox('Add cumulative lines below', value=False, key=f'{key}_cum')
        if not pick: st.info('Pick at least one series.'); return
        cum_cols = [('Cum ' + m) for m in pick if ('Cum ' + m) in conv.columns] if cumul else None
        # Cum columns were renamed by convert: look them up by their label instead
        if cumul:
            cum_cols = [c for c in conv.columns if c.startswith('Cum ') and any(c.replace('Cum ', '') == m for m in pick)]
        fig = annual.bar_figure(conv, pick, 'Yearly volumes - field', cumulative=cum_cols or None, stacked=stacked)
        st.plotly_chart(fig, use_container_width=True, key=f'{key}_fig'); shown = conv
    else:
        m = st.selectbox('Quantity', measures, key=f'{key}_meas')
        names = sorted(conv[ent].unique()); pick = st.multiselect(src, names, default=names, key=f'{key}_ents')
        if not pick: st.info(f'Pick at least one {ent.lower()}.'); return
        wide = conv[conv[ent].isin(pick)].pivot_table(index='Year', columns=ent, values=m, aggfunc='sum').reset_index().fillna(0.0)
        part = conv[['Year', 'Partial']].groupby('Year')['Partial'].max() if 'Partial' in conv else None
        wide['Partial'] = wide['Year'].map(part) if part is not None else False
        wide['Total'] = wide[pick].sum(axis=1)
        fig = annual.bar_figure(wide, pick, f'{m.split(" [")[0]} by {ent.lower()} per year', stacked=stacked)
        st.plotly_chart(fig, use_container_width=True, key=f'{key}_fig'); shown = wide
    st.dataframe(shown, hide_index=True, use_container_width=True)
    table_actions(st, shown, f'yearly_{src.lower()}', key=f'{key}_ta')
