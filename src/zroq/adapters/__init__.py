"""ZORQ adapter package.

Phase 3B.2 placement decision (DQ-2): ``src/zroq`` core keeps **zero**
imports of the MEMORY//OS backend. The production adapter lives here, in
``zroq.adapters.memoryos_v10``, and is the single module permitted to
import ``backend/app`` (lazily, by repository path) — see
``memoryos_v10.py`` for the exact boundary contract.
"""

from .memoryos_v10 import (
    MEMORYOS_V10_ADAPTER_VERSION,
    MemoryOSCompositionError,
    MemoryOSV10Composition,
    OwnerIdentityMapping,
    ProductionMemoryOSAdapter,
    ProductionMemoryOSContractAdapter,
    compose_production_memoryos,
)

__all__ = [
    "MEMORYOS_V10_ADAPTER_VERSION",
    "MemoryOSCompositionError",
    "MemoryOSV10Composition",
    "OwnerIdentityMapping",
    "ProductionMemoryOSAdapter",
    "ProductionMemoryOSContractAdapter",
    "compose_production_memoryos",
]
