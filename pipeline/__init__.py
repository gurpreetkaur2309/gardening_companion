"""Catalog data pipeline: discover -> extract -> clean -> transform -> validate -> load.

Runs offline from the web app (``python -m pipeline run``).  Each stage reads
and writes JSONL under a work directory, so any stage can be re-run on its own
and every rejected record keeps the reason it was dropped.
"""
