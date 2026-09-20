"""The briefing: weather, markets and headlines, compiled on request.

Nothing here polls. The canvas asks, the agent reads the pages once and writes
`data/ambient.json`, and this service serves that file and tells the canvas it
changed. See `service.py`.
"""

from .service import AmbientService

__all__ = ["AmbientService"]
