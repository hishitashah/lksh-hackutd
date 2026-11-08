"""Avon Nightwatch backend package.

Modules (see plan.md "Architecture & stack" for the full map):

* ``config``  - settings read from environment variables / ``.env``
* ``models``  - the data contract shared with the frontend and the agent (T0.2)
* ``mocks``   - fixture loader used while the real engine is being built (T0.3)
* ``api``     - the FastAPI app the frontend talks to (T0.1, finished in M4)

Later milestones add ``data`` (M1), ``detect`` (M2), ``sim`` / ``optimize`` /
``replay`` (M3) and ``agent/`` (M5).
"""

__version__ = "0.1.0"
