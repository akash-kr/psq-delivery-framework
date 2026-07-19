# Delivery Proof Workspace

This directory is reserved for run-specific grounding and proof artifacts.

Generated logs and screenshots are ignored by default. A project may choose to commit selected proof reports under `docs/qa/` or another durable review path.

The escalation watcher also writes `escalation-state.json` here. Keep that file in persistent scheduler storage (but out of git) so active alerts are deduplicated and recovery notifications survive process restarts.
