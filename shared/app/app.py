import sys
from pathlib import Path

# Ensure /app/src is importable (so "import draftboard" works)
THIS_DIR = Path(__file__).resolve().parent
SRC_DIR = THIS_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import streamlit as st

# Sidebar behavior:
# - commissioner URL => start collapsed
# - non-commissioner URL => default/auto
is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"

st.set_page_config(
    page_title="Major League Fantasy Draft Board",
    layout="wide",
    initial_sidebar_state=("collapsed" if is_commissioner_url else "auto"),
)

from draftboard.ui.app import render_app

render_app()
