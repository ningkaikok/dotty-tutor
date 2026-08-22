"""HTTP adapter layer.

The API package owns transport concerns only.  Routers validate requests,
translate application results to HTTP responses, and keep business workflows
out of the FastAPI composition root.
"""

