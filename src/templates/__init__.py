"""Strategy template library — parameterized factories that produce `Strategy`
instances from a validated `StrategySpec`.

This is the trust boundary for AI-generated strategies: the synthesis agent
picks a template id and fills in parameter slots; deterministic Python
validates and instantiates. The agent never writes free-form Python that
trades capital.

See `docs/system-architecture.md` for the full rationale.
"""

from src.templates.base import (
    ParamSpec,
    StrategyTemplate,
    TemplateRegistryError,
    all_registered,
    get_template,
    register,
)

# Side-effect imports to populate the registry.
from src.templates import (  # noqa: F401
    ma_cross,
    mean_reversion_rsi,
    momentum_zscore,
    vol_breakout,
)

__all__ = [
    "ParamSpec",
    "StrategyTemplate",
    "TemplateRegistryError",
    "all_registered",
    "get_template",
    "register",
]
