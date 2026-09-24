"""Workspace settings (Settings → General and Approvals): defaults, bounds, and what each one changes.

Stored values live in the store's ``settings`` table; anything never changed takes its default here.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

DEFAULTS: dict[str, Any] = {
    "workspace_name": "Fleet Control",  # shown under the product name in the top bar
    "session_hours": 12,  # how long a sign-in lasts without activity; applies to new sign-ins
    "approvals_production": 2,  # the floor for production plans (build document §7); a blueprint target may ask for more
    "approvals_staging": 0,
    "approvals_lab": 0,
    # a blueprint's agent tests must have passed on lab or staging before a production apply (build document §9)
    "require_tests_for_production": True,
    "token_max_days": 90,  # the longest lifetime a new API token may be given
    "portal_url": "http://localhost:5173",
    # Messaging on or off for the whole workspace: off hides it from the menu, stops every delivery and refuses its API;
    # channels and rules are kept, so turning it back on resumes where it was
    "messaging_enabled": True,  # where links in messages point (Messaging); the address people open
}


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_name: Optional[str] = Field(None, min_length=1, max_length=80)
    session_hours: Optional[int] = Field(None, ge=1, le=24)
    approvals_production: Optional[int] = Field(None, ge=1, le=5, description="Production always needs at least one approval.")
    approvals_staging: Optional[int] = Field(None, ge=0, le=5)
    approvals_lab: Optional[int] = Field(None, ge=0, le=5)
    require_tests_for_production: Optional[bool] = None
    token_max_days: Optional[int] = Field(None, ge=1, le=365)
    messaging_enabled: Optional[bool] = None
    portal_url: Optional[str] = Field(None, max_length=200, pattern=r"^https?://[^\s/]+(/[^\s]*)?$")


def effective(stored: dict[str, Any]) -> dict[str, Any]:
    """Every setting: the stored value, or its default."""
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


def approval_floor(values: dict[str, Any]) -> dict[str, int]:
    """Minimum approvals per environment, for planner.compute_plan."""
    return {"production": values["approvals_production"], "staging": values["approvals_staging"], "lab": values["approvals_lab"]}
