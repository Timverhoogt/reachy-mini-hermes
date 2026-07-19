"""Fail-closed policy primitives for Reachy-facing agent capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType


class CapabilityId(StrEnum):
    """Stable broker capability identifiers; availability is defined separately."""

    GET_AGENT_CAPABILITIES = "get_agent_capabilities"
    GET_REACHY_STATUS = "get_reachy_status"
    GET_HOME_STATUS = "get_home_status"
    SEARCH_CURRENT_INFORMATION = "search_current_information"
    READ_PUBLIC_WEB_PAGE = "read_public_web_page"
    RECALL_PERSONAL_CONTEXT = "recall_personal_context"
    SEARCH_CONVERSATION_HISTORY = "search_conversation_history"
    READ_SCOPED_NOTE = "read_scoped_note"


class RiskTier(IntEnum):
    T0_PUBLIC_READ = 0
    T1_PRIVATE_READ = 1
    T2_BOUNDED_LOCAL_ACTION = 2
    T3_EXTERNAL_SIDE_EFFECT = 3
    T4_PRIVILEGED = 4


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability_id: CapabilityId
    risk_tier: RiskTier
    description: str
    requires_camera: bool = False
    requires_approval: bool = False
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Authoritative state captured immediately before execution."""

    capability_profile: str = "conversation"
    adult_ui_unlocked: bool = False
    kids_mode_active: bool = False
    power_mode: str = "standby"
    privacy_enabled: bool = True
    emergency_stop_active: bool = False
    robot_available: bool = True
    camera_permitted: bool = False
    approval_granted: bool = False
    session_generation: int = 0
    requested_session_generation: int = 0


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str
    risk_tier: RiskTier | None = None


# Phase 0 deliberately enables no broker capability. This inventory is truthful
# about the planned typed surface without granting authority before Phase 1.
PHASE_0_CAPABILITIES: Mapping[CapabilityId, CapabilityDefinition] = MappingProxyType(
    {
        capability: CapabilityDefinition(
            capability_id=capability,
            risk_tier=RiskTier.T1_PRIVATE_READ
            if capability in {
                CapabilityId.GET_HOME_STATUS,
                CapabilityId.RECALL_PERSONAL_CONTEXT,
                CapabilityId.SEARCH_CONVERSATION_HISTORY,
                CapabilityId.READ_SCOPED_NOTE,
            }
            else RiskTier.T0_PUBLIC_READ,
            description=capability.value.replace("_", " "),
            enabled=False,
        )
        for capability in CapabilityId
    }
)


@dataclass(slots=True)
class AgentPolicy:
    definitions: Mapping[CapabilityId, CapabilityDefinition] = field(
        default_factory=lambda: PHASE_0_CAPABILITIES
    )

    def enabled_capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(item for item in self.definitions.values() if item.enabled)

    def decide(self, capability: CapabilityId, context: PolicyContext) -> PolicyDecision:
        definition = self.definitions.get(capability)
        if definition is None or not definition.enabled:
            return PolicyDecision(False, "capability_disabled")
        if context.requested_session_generation != context.session_generation:
            return PolicyDecision(False, "stale_session", definition.risk_tier)
        if context.capability_profile != "agent":
            return PolicyDecision(False, "agent_profile_inactive", definition.risk_tier)
        if not context.adult_ui_unlocked or context.kids_mode_active:
            return PolicyDecision(False, "adult_ui_required", definition.risk_tier)
        if context.power_mode in {"meeting", "sleep"}:
            return PolicyDecision(False, "power_mode_blocked", definition.risk_tier)
        if not context.privacy_enabled:
            return PolicyDecision(False, "privacy_disabled", definition.risk_tier)
        if context.emergency_stop_active:
            return PolicyDecision(False, "emergency_stop", definition.risk_tier)
        if not context.robot_available:
            return PolicyDecision(False, "robot_unavailable", definition.risk_tier)
        if definition.requires_camera and not context.camera_permitted:
            return PolicyDecision(False, "camera_not_permitted", definition.risk_tier)
        if definition.requires_approval and not context.approval_granted:
            return PolicyDecision(False, "approval_required", definition.risk_tier)
        return PolicyDecision(True, "allowed", definition.risk_tier)
