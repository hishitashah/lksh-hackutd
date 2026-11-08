"""Avon Nightwatch backend package.

Modules (ARCHITECTURE.md §14 has the full map):

* ``config``  - settings read from environment variables / ``.env``
* ``models``  - the data contract shared with the frontend (ARCHITECTURE.md §12.1)
* ``mocks``   - fixture loader used while the real engine is being built (clearly labelled mock data)
* ``api``     - the FastAPI app the frontend talks to

Next, per ARCHITECTURE.md §14: ``monitoring/`` (Stage 1), ``agents/`` + ``orchestration/`` (Stages 2-3),
``planning/`` (deterministic costs) and ``services/`` (dispatch).
"""

__version__ = "0.1.0"
