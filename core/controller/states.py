"""
TAOS FSM States — All possible controller states.
Re-exports from constants for convenience.
"""

from taos.config.constants import FSMState

# All states available via:
#   from taos.core.controller.states import FSMState
#
# States:
#   INIT → PLANNING → PLAN_READY → EXECUTING → REFLECTING → TERMINATING → TERMINATED
#   Any state → FAILED (on unrecoverable error)
#   REFLECTING → REPLANNING → PLAN_READY (on dynamic replan)

__all__ = ["FSMState"]
