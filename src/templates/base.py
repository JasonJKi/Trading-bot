"""StrategyTemplate ABC + parameter schema + registry.

A template is a parameterized factory. Given a `StrategySpec` (template id +
params + universe + schedule), it returns a `Strategy` instance the
orchestrator can run. The bridge between AI-proposed configurations and
deterministic execution.

Design goals:
  - Parameter ranges are declared, not coded — the synthesis agent reads them
    to know what's legal; deterministic validation enforces them at instantiate.
  - Templates are CODE not DATA — they're discovered by import-time registration,
    not stored in the DB. Adding a template = adding a Python file.
  - Each template is small. Big strategies should compose multiple templates
    via the orchestrator's per-bot allocation, not via mega-templates.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Literal

from src.core.strategy import Strategy


# --------------------------------------------------------------------------------
# Parameter spec — declarative schema the synthesis agent and validator both read.
# --------------------------------------------------------------------------------

@dataclass(slots=True)
class ParamSpec:
    """Declares one tunable parameter of a template.

    The `kind` field tells the synthesis agent what value shape to produce:
      - "int" / "float": numeric, with `low`/`high` bounds (inclusive)
      - "choice":        one of `choices`
      - "bool":          True / False

    `default` is used when the spec doesn't provide a value (or for tests).
    """

    name: str
    kind: Literal["int", "float", "choice", "bool"]
    description: str
    default: Any
    low: float | int | None = None
    high: float | int | None = None
    choices: list[Any] = field(default_factory=list)

    def validate(self, value: Any) -> Any:
        """Coerce + validate a candidate value. Raises ValueError on bad input."""
        if self.kind == "int":
            v = int(value)
            if self.low is not None and v < int(self.low):
                raise ValueError(f"{self.name}={v} < low={self.low}")
            if self.high is not None and v > int(self.high):
                raise ValueError(f"{self.name}={v} > high={self.high}")
            return v
        if self.kind == "float":
            v = float(value)
            if self.low is not None and v < float(self.low):
                raise ValueError(f"{self.name}={v} < low={self.low}")
            if self.high is not None and v > float(self.high):
                raise ValueError(f"{self.name}={v} > high={self.high}")
            return v
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.name}={value!r} not in {self.choices}")
            return value
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y"}
            return bool(value)
        raise ValueError(f"unknown kind: {self.kind}")

    def to_dict(self) -> dict:
        out = {"name": self.name, "kind": self.kind, "description": self.description, "default": self.default}
        if self.low is not None:
            out["low"] = self.low
        if self.high is not None:
            out["high"] = self.high
        if self.choices:
            out["choices"] = self.choices
        return out


# --------------------------------------------------------------------------------
# Template ABC + registry.
# --------------------------------------------------------------------------------

class StrategyTemplate(ABC):
    """A parameterized factory that produces a Strategy from a spec."""

    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]            # one paragraph, plain English. The synthesis agent reads this.
    version: ClassVar[str] = "1"
    category: ClassVar[Literal["trend", "mean_reversion", "momentum", "breakout", "factor", "other"]] = "other"
    asset_classes: ClassVar[list[str]] = ["equity"]   # equity / crypto / etf

    @classmethod
    @abstractmethod
    def param_specs(cls) -> list[ParamSpec]:
        """Declarative parameter list. Order is stable across versions."""

    @classmethod
    def default_schedule(cls) -> dict:
        """APScheduler cron fields. Override for sub-daily strategies."""
        return {"day_of_week": "mon-fri", "hour": "20", "minute": "15"}

    @classmethod
    def default_universe(cls) -> list[str]:
        """Sensible default symbols. The spec usually overrides this."""
        return ["SPY", "QQQ", "IWM", "DIA"]

    @classmethod
    def validate_params(cls, params: dict) -> dict:
        """Coerce + validate a dict of params. Returns a clean dict.

        Unknown keys are dropped. Missing keys fall back to the param's default.
        Invalid values raise ValueError immediately.
        """
        out: dict[str, Any] = {}
        specs = {p.name: p for p in cls.param_specs()}
        for name, spec in specs.items():
            if name in params:
                out[name] = spec.validate(params[name])
            else:
                out[name] = spec.default
        return out

    @classmethod
    def schema_for_agent(cls) -> dict:
        """JSON-friendly description for the synthesis agent's prompt."""
        return {
            "id": cls.id,
            "name": cls.name,
            "version": cls.version,
            "category": cls.category,
            "asset_classes": cls.asset_classes,
            "description": cls.description,
            "params": [p.to_dict() for p in cls.param_specs()],
            "default_schedule": cls.default_schedule(),
        }

    @classmethod
    @abstractmethod
    def instantiate(
        cls,
        *,
        bot_id: str,
        params: dict,
        universe: list[str],
        schedule: dict | None = None,
        version: str | None = None,
    ) -> Strategy:
        """Build a Strategy instance from a spec.

        Implementations should:
          1. Call `cls.validate_params(params)` to clean/validate.
          2. Build and return a Strategy subclass instance whose `id`, `name`,
             `version`, `schedule`, `universe`, and `target_positions` reflect
             the spec.

        `bot_id` becomes Strategy.id (the foreign key the rest of the system
        uses for attribution). `version` defaults to `cls.version`.
        """


# --------------------------------------------------------------------------------
# Registry — populated by side-effect imports in `src/templates/__init__.py`.
# --------------------------------------------------------------------------------

class TemplateRegistryError(LookupError):
    """Raised when a template id is unknown."""


_REGISTRY: dict[str, type[StrategyTemplate]] = {}


def register(cls: type[StrategyTemplate]) -> type[StrategyTemplate]:
    """Decorator — add a template class to the registry by its `id`."""
    if not getattr(cls, "id", None):
        raise ValueError(f"{cls.__name__} must define a class-level `id`")
    if cls.id in _REGISTRY and _REGISTRY[cls.id] is not cls:
        raise ValueError(f"template id collision: {cls.id!r}")
    _REGISTRY[cls.id] = cls
    return cls


def get_template(template_id: str) -> type[StrategyTemplate]:
    """Look up a template class by id. Raises TemplateRegistryError if missing."""
    try:
        return _REGISTRY[template_id]
    except KeyError as exc:
        raise TemplateRegistryError(f"unknown template id: {template_id!r}") from exc


def all_registered() -> dict[str, type[StrategyTemplate]]:
    """All registered templates. Used by the dashboard, CLI, and synthesis agent."""
    return dict(_REGISTRY)


# --------------------------------------------------------------------------------
# Helper: a small functional Strategy subclass templates can build on.
# --------------------------------------------------------------------------------

class _FunctionalStrategy(Strategy):
    """A Strategy whose target_positions delegates to a closure.

    Templates use this so the *factory* is the visible class while the
    *instances* don't pollute the module namespace with anonymous subclasses.
    """

    def __init__(
        self,
        *,
        bot_id: str,
        name: str,
        version: str,
        schedule: dict,
        universe: list[str],
        params: dict,
        target_fn: Callable[[Strategy, "object"], list],
    ) -> None:
        super().__init__(params)
        self.id = bot_id
        self.name = name
        self.version = version
        self.schedule = schedule
        self._universe = list(universe)
        self._target_fn = target_fn

    def universe(self) -> list[str]:
        return list(self._universe)

    def target_positions(self, ctx):  # type: ignore[override]
        return self._target_fn(self, ctx)
