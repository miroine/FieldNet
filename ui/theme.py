THEMES = {
    'Equinor-inspired Light': {'bg':'#f7f7f7','surface':'#ffffff','text':'#1f1f1f','muted':'#6b6b6b','accent':'#d71920','accent2':'#007079','border':'#dedede'},
    'Equinor-inspired Dark': {'bg':'#171717','surface':'#242424','text':'#f5f5f5','muted':'#b7b7b7','accent':'#ff4b55','accent2':'#62c6c9','border':'#444444'},
    'North Sea': {'bg':'#eef5f7','surface':'#ffffff','text':'#15323b','muted':'#60747a','accent':'#006f7b','accent2':'#c43d31','border':'#cbdcdf'},
    'Classic FieldNet': {'bg':'#ffffff','surface':'#f7f8fa','text':'#20242a','muted':'#6a7078','accent':'#2563eb','accent2':'#0f766e','border':'#d8dde5'},
}

def apply_theme(st, name):
    t=THEMES.get(name, THEMES['Equinor-inspired Light'])
    st.markdown(f'''<style>
    .stApp {{background:{t['bg']}; color:{t['text']};}}
    [data-testid="stSidebar"] {{background:{t['surface']}; border-right:1px solid {t['border']};}}
    div[data-testid="stMetric"] {{background:{t['surface']};border:1px solid {t['border']};border-radius:4px;padding:12px;}}
    .stButton>button {{border-radius:4px;border:1px solid {t['border']};}}
    .stButton>button[kind="primary"] {{background:{t['accent']};border-color:{t['accent']};}}
    .fieldnet-brand {{padding:14px 16px;border:1px solid {t['border']};border-left:4px solid {t['accent']};border-radius:4px;background:{t['surface']};margin-bottom:12px;}}
    .fieldnet-brand h2 {{margin:0;color:{t['text']};font-size:1.35rem;}}
    .fieldnet-brand p {{margin:.25rem 0 0;color:{t['muted']};font-size:.88rem;}}
    </style>''', unsafe_allow_html=True)
    return t
