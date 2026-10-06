"""Compressor optimization UI component for T2.4 integration.

Provides interactive compressor performance visualization and multi-speed
optimization scenarios for the Streamlit app.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from typing import Dict, List, Tuple, Optional

try:
    from optimization.compressor_affinity import CompressorAffinityModel, create_axial_compressor_map, create_centrifugal_compressor_map
    from solver.gas_network import GasNetworkSolver
    COMPRESSOR_AFFINITY_AVAILABLE = True
except ImportError:
    COMPRESSOR_AFFINITY_AVAILABLE = False


def render_compressor_optimization(st_session, nodes: List[Dict], edges: List[Dict], unit_profile: str = 'norwegian_si'):
    """Render compressor optimization tab with operating maps and scenario comparison.

    Args:
        st_session: Streamlit session state
        nodes: Network nodes (wells, reservoirs, boundaries)
        edges: Network connections (pipelines, compressors)
        unit_profile: Unit system ('norwegian_si', 'english_oilfield', etc.)
    """

    if not COMPRESSOR_AFFINITY_AVAILABLE:
        st.info('Compressor Affinity module not available. Install or enable the module.')
        return

    # Find all compressor edges in the network
    compressor_edges = [e for e in edges if e.get('kind') == 'compressor']

    if not compressor_edges:
        st.info('No compressors found in the network. Add a compressor component to enable optimization.')
        return

    st.subheader('Compressor Performance & Speed Optimization')
    st.caption(
        'Visualize compressor operating envelopes (surge/runout lines) and optimize '
        'speed for maximum efficiency or to avoid operating constraints.'
    )

    # Compressor selection
    comp_map = {e['id']: e.get('name', e['id']) for e in compressor_edges}
    selected_comp_id = st.selectbox(
        'Select compressor',
        list(comp_map.keys()),
        format_func=lambda k: comp_map[k],
        key='comp_opt_select'
    )
    selected_comp = next(e for e in compressor_edges if e['id'] == selected_comp_id)

    # Create two columns: left for controls, right for visualization
    col_controls, col_viz = st.columns([1.2, 2.8])

    with col_controls:
        st.markdown('### Operating Point')

        # Compressor type selection
        comp_type = st.radio(
            'Compressor type',
            ['Axial', 'Centrifugal'],
            key=f'comp_type_{selected_comp_id}'
        )

        # Create reference map based on type
        if comp_type == 'Axial':
            ref_map = create_axial_compressor_map()
        else:
            ref_map = create_centrifugal_compressor_map()

        # Current operating speed
        current_speed = st.number_input(
            f'Current speed [RPM]',
            min_value=3000.0,
            max_value=10500.0,
            value=float(ref_map.speed_rpm),
            step=100.0,
            key=f'comp_speed_{selected_comp_id}'
        )

        # Create affinity model
        affinity_model = CompressorAffinityModel(
            reference_map=ref_map,
            reference_speed_rpm=ref_map.speed_rpm
        )

        st.markdown('### Optimization Targets')

        # Speed optimization target
        optimize_target = st.selectbox(
            'Optimization objective',
            ['Maximize head margin', 'Maximize efficiency', 'Minimize power', 'Custom speed'],
            key=f'comp_opt_target_{selected_comp_id}'
        )

        if optimize_target == 'Custom speed':
            target_speed = st.number_input(
                'Target speed [RPM]',
                min_value=3000.0,
                max_value=10500.0,
                value=current_speed,
                step=100.0,
                key=f'comp_target_speed_{selected_comp_id}'
            )
        else:
            target_speed = current_speed

        # Operating point parameters
        st.markdown('### Inlet Conditions')
        inlet_pressure_bar = st.number_input(
            'Inlet pressure [bar]',
            min_value=1.0,
            max_value=100.0,
            value=20.0,
            step=1.0,
            key=f'comp_inlet_p_{selected_comp_id}'
        )

        inlet_flow_sm3d = st.number_input(
            f'Inlet flow [Sm³/d]',
            min_value=100.0,
            max_value=3000.0,
            value=float(ref_map.rated_flow_sm3d),
            step=50.0,
            key=f'comp_inlet_q_{selected_comp_id}'
        )

        # Display surge/runout info
        st.markdown('### Envelope Protection')
        surge_flow = affinity_model.scale_flow(
            ref_map.surge_flow_sm3d,
            ref_map.speed_rpm,
            target_speed
        )
        runout_flow = affinity_model.scale_flow(
            ref_map.runout_flow_sm3d,
            ref_map.speed_rpm,
            target_speed
        )

        env_col1, env_col2 = st.columns(2)
        with env_col1:
            st.metric('Surge limit [Sm³/d]', f'{surge_flow:,.0f}')
        with env_col2:
            st.metric('Runout limit [Sm³/d]', f'{runout_flow:,.0f}')

        # Operating point status
        if inlet_flow_sm3d < surge_flow:
            st.warning('⚠️ Operating below surge line — compressor stall risk')
        elif inlet_flow_sm3d > runout_flow:
            st.warning('⚠️ Operating above runout line — choke risk')
        else:
            st.success('✓ Within stable operating envelope')

    with col_viz:
        st.markdown('### Operating Map & Performance')

        # Generate operating map at target speed
        op_map = affinity_model.operating_map(
            target_speed_rpm=target_speed,
            flow_range=(surge_flow * 0.8, runout_flow * 1.1),
            n_points=60
        )

        # Create interactive plot
        fig = go.Figure()

        # Add head curve
        fig.add_trace(go.Scatter(
            x=op_map['flows'],
            y=op_map['heads'],
            mode='lines',
            name='Head [bar]',
            line=dict(color='#1f77b4', width=3),
            hovertemplate='Flow: %{x:.0f} Sm³/d<br>Head: %{y:.1f} bar<extra></extra>'
        ))

        # Add efficiency curve (secondary axis)
        fig.add_trace(go.Scatter(
            x=op_map['flows'],
            y=op_map['efficiencies'],
            mode='lines',
            name='Efficiency [-]',
            line=dict(color='#ff7f0e', width=3, dash='dash'),
            yaxis='y2',
            hovertemplate='Flow: %{x:.0f} Sm³/d<br>Efficiency: %{y:.2%}<extra></extra>'
        ))

        # Mark operating point
        if surge_flow <= inlet_flow_sm3d <= runout_flow:
            h, eff, p, status = affinity_model.get_performance_at_speed(target_speed, inlet_flow_sm3d)
            if status == 'OK' and h is not None:
                fig.add_trace(go.Scatter(
                    x=[inlet_flow_sm3d],
                    y=[h],
                    mode='markers',
                    name='Current operating point',
                    marker=dict(size=14, color='#2ca02c', symbol='diamond', line=dict(width=2, color='white')),
                    hovertemplate='Flow: %{x:.0f} Sm³/d<br>Head: %{y:.1f} bar<extra></extra>'
                ))

        # Mark surge and runout lines
        fig.add_vline(
            x=surge_flow,
            line_dash='dash',
            line_color='red',
            annotation_text='Surge',
            annotation_position='top left',
            opacity=0.6
        )
        fig.add_vline(
            x=runout_flow,
            line_dash='dash',
            line_color='orange',
            annotation_text='Runout',
            annotation_position='top right',
            opacity=0.6
        )

        # Update layout with dual y-axis
        fig.update_layout(
            title=f'{comp_type} Compressor @ {target_speed:.0f} RPM',
            xaxis_title='Flow [Sm³/d]',
            yaxis_title='Head [bar]',
            yaxis2=dict(
                title='Efficiency [-]',
                overlaying='y',
                side='right',
                range=[0.5, 1.0]
            ),
            hovermode='x unified',
            height=450,
            showlegend=True
        )

        st.plotly_chart(fig, use_container_width=True)

    # Multi-speed envelope visualization
    st.markdown('---')
    st.markdown('### Speed Envelope & Optimization')

    envelope_speeds = st.slider(
        'Speed range for envelope [RPM]',
        min_value=3000,
        max_value=10500,
        value=(5000, 8000),
        step=500,
        key=f'envelope_range_{selected_comp_id}'
    )

    # Generate optimization envelope
    envelope = affinity_model.optimization_envelope(
        speed_range=tuple(envelope_speeds),
        n_speeds=7
    )

    # Create envelope plot
    env_fig = go.Figure()

    speeds = [e['speed_rpm'] for e in envelope]
    surge_flows = [e['surge_flow_sm3d'] for e in envelope]
    runout_flows = [e['runout_flow_sm3d'] for e in envelope]

    env_fig.add_trace(go.Scatter(
        x=speeds,
        y=surge_flows,
        mode='lines+markers',
        name='Surge line',
        line=dict(color='red', width=2),
        marker=dict(size=8),
        hovertemplate='Speed: %{x:.0f} RPM<br>Surge: %{y:.0f} Sm³/d<extra></extra>'
    ))

    env_fig.add_trace(go.Scatter(
        x=speeds,
        y=runout_flows,
        mode='lines+markers',
        name='Runout line',
        line=dict(color='orange', width=2),
        marker=dict(size=8),
        hovertemplate='Speed: %{x:.0f} RPM<br>Runout: %{y:.0f} Sm³/d<extra></extra>'
    ))

    # Fill between curves
    env_fig.add_trace(go.Scatter(
        x=speeds + speeds[::-1],
        y=surge_flows + runout_flows[::-1],
        fill='toself',
        fillcolor='rgba(0, 100, 200, 0.1)',
        line=dict(color='rgba(255, 255, 255, 0)'),
        showlegend=False,
        name='Safe operating envelope'
    ))

    env_fig.update_layout(
        title='Compressor Operating Envelope vs Speed',
        xaxis_title='Compressor Speed [RPM]',
        yaxis_title='Flow [Sm³/d]',
        height=400,
        hovermode='x unified'
    )

    st.plotly_chart(env_fig, use_container_width=True)

    # Speed optimization scenarios
    st.markdown('---')
    st.markdown('### Speed Optimization Scenarios')

    scenario_col1, scenario_col2 = st.columns(2)

    with scenario_col1:
        st.markdown('**Scenario 1: Maximum Head**')
        # Find speed that maximizes head at current flow
        best_head = None
        best_speed = None
        best_eff = None

        for e in envelope:
            speed = e['speed_rpm']
            if e['surge_flow_sm3d'] <= inlet_flow_sm3d <= e['runout_flow_sm3d']:
                h, eff, p, status = affinity_model.get_performance_at_speed(speed, inlet_flow_sm3d)
                if status == 'OK' and h is not None:
                    if best_head is None or h > best_head:
                        best_head = h
                        best_speed = speed
                        best_eff = eff

        if best_speed is not None:
            st.metric('Optimal speed', f'{best_speed:.0f} RPM')
            st.metric('Head available', f'{best_head:.1f} bar')
            st.metric('Efficiency', f'{best_eff:.1%}')
        else:
            st.caption('No valid operating point found in envelope')

    with scenario_col2:
        st.markdown('**Scenario 2: Maximum Efficiency**')
        # Find speed that maximizes efficiency at current flow
        best_eff_max = None
        best_speed_eff = None
        best_head_eff = None

        for e in envelope:
            speed = e['speed_rpm']
            if e['surge_flow_sm3d'] <= inlet_flow_sm3d <= e['runout_flow_sm3d']:
                h, eff, p, status = affinity_model.get_performance_at_speed(speed, inlet_flow_sm3d)
                if status == 'OK' and eff is not None:
                    if best_eff_max is None or eff > best_eff_max:
                        best_eff_max = eff
                        best_speed_eff = speed
                        best_head_eff = h

        if best_speed_eff is not None:
            st.metric('Optimal speed', f'{best_speed_eff:.0f} RPM')
            st.metric('Head available', f'{best_head_eff:.1f} bar')
            st.metric('Efficiency', f'{best_eff_max:.1%}')
        else:
            st.caption('No valid operating point found in envelope')

    # Scenario comparison table
    st.markdown('---')
    st.markdown('### Performance at Different Speeds')

    scenario_speeds = st.multiselect(
        'Compare speeds [RPM]',
        [int(e['speed_rpm']) for e in envelope],
        default=[int(envelope[0]['speed_rpm']), int(envelope[-1]['speed_rpm'])],
        key=f'scenario_speeds_{selected_comp_id}'
    )

    if scenario_speeds:
        scenarios = []
        for speed in sorted(scenario_speeds):
            h, eff, p, status = affinity_model.get_performance_at_speed(speed, inlet_flow_sm3d)
            if status == 'OK':
                scenarios.append({
                    'Speed [RPM]': int(speed),
                    'Head [bar]': round(h, 2) if h else None,
                    'Efficiency [-]': round(eff, 3) if eff else None,
                    'Power [kW]': round(p, 1) if p else None,
                    'Status': status
                })
            else:
                scenarios.append({
                    'Speed [RPM]': int(speed),
                    'Head [bar]': None,
                    'Efficiency [-]': None,
                    'Power [kW]': None,
                    'Status': status
                })

        scenario_df = pd.DataFrame(scenarios)
        st.dataframe(scenario_df, use_container_width=True, hide_index=True)


# Legacy function for integration with existing app structure
def render_compressor_tab(st_session, nodes: List[Dict], edges: List[Dict], unit_profile: str = 'norwegian_si'):
    """Entry point for compressor optimization tab (T2.4 UI integration)."""
    render_compressor_optimization(st_session, nodes, edges, unit_profile)
