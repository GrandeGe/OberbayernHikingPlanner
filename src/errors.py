from __future__ import annotations


class HikingPlannerError(Exception):
    """Base for every error this project raises deliberately."""


class DataSourceError(HikingPlannerError):
    """An upstream API/file was unreachable or returned garbage."""


class NotFoundError(HikingPlannerError):
    """A lookup succeeded mechanically but produced no usable result."""


class DatabaseError(HikingPlannerError):
    """Local database missing, stale, or schema-mismatched."""
