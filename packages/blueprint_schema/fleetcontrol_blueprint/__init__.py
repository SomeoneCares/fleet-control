"""Fleet Blueprint schema v1 — the desired-state model for a Hermes Agent fleet.

Pydantic models are the single source of truth. They validate YAML/JSON blueprints,
export JSON Schema for editors and CI, and are shared by the API, the Fleet Control
Agent and the web client (via the exported schema).

Design rules encoded here:
- Secrets are references (``secret://...``), never values.
- SOUL text is stored with a content hash so drift on SOUL is detectable.
- Every write-capable field is a "managed field"; the agent only reconciles those.
"""

from .models import (
    API_VERSION,
    Agent,
    Blueprint,
    BlueprintMetadata,
    DeliveryRule,
    HumanGate,
    ModelPolicy,
    OutputContract,
    Policy,
    Requires,
    Soul,
    Target,
    Test,
    TestLimits,
    Workflow,
    WorkflowStep,
    load_blueprint,
    dump_blueprint,
    json_schema,
)

__all__ = [
    "API_VERSION",
    "Agent",
    "Blueprint",
    "BlueprintMetadata",
    "DeliveryRule",
    "HumanGate",
    "ModelPolicy",
    "OutputContract",
    "Policy",
    "Requires",
    "Soul",
    "Target",
    "Test",
    "TestLimits",
    "Workflow",
    "WorkflowStep",
    "load_blueprint",
    "dump_blueprint",
    "json_schema",
]
