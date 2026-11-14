"""Errors raised by the scheduling tools. All subclass ValueError."""
from __future__ import annotations


class SchedulingError(ValueError):
    """Base class: the input can't be scheduled. Message is safe to show the PM."""


class DuplicateTaskError(SchedulingError):
    pass


class UnknownDependencyError(SchedulingError):
    pass


class SelfDependencyError(SchedulingError):
    pass


class MissingDurationError(SchedulingError):
    pass


class InvalidInputError(SchedulingError):
    pass


class CycleError(SchedulingError):
    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("Dependency cycle: " + " -> ".join(cycle))
