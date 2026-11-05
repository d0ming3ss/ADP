"""GeoRent-AI — punkt wejścia aplikacji Streamlit (``streamlit run app.py``)."""

import logging

from georent.ui import run_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

run_app()
