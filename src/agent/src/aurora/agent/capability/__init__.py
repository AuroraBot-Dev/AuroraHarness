"""导出 Agent 能力特化的公共接口。"""

from .specialization import (
    Capability,
    CapabilityContext,
    CapabilityPolicy,
    CapabilityRegistry,
    CapabilityResolver,
    CapabilityScope,
    ResolvedCapabilities,
)

__all__ = [
    "Capability",
    "CapabilityContext",
    "CapabilityPolicy",
    "CapabilityRegistry",
    "CapabilityResolver",
    "CapabilityScope",
    "ResolvedCapabilities",
]
