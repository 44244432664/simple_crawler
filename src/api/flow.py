"""Safe, ordered JSON flow engine with an allowlisted module registry.

Task 2 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

Design
------
* A stored site format's ``flow`` is an ordered list of step objects::

      {"module": "crawl_chapter",
       "params": {"expected_selector": ".chapter-content",
                  "actions": ["crawl_text_content", "download_image"]},
       "when": {"content_type": {"in": ["novel", "comic"]}}}

* ``build_flow`` validates the whole flow *before* any network request or
  output-directory creation.  Malformed flows raise a specific deterministic
  error (:class:`api.contracts.MissingFlowError` for flow-less legacy formats,
  :class:`api.contracts.InvalidFlowError` otherwise).
* Only registered modules can run.  Conditions only compare allowlisted
  request fields using equality/membership operators — arbitrary expressions,
  imports, code, or object values are rejected.
* The mandatory stage sequence ``metadata -> volume preparation -> chapter
  crawl -> export`` must be declared in order, exactly once each.  A step
  whose ``when`` condition evaluates false is skipped at execution time but the
  flow stays valid (conditionally skipped required modules).
* ``get_user_input`` is a runtime-only module: interactive TUI execution may
  prepend it as the first step; stored flows never contain it.

Flow API
--------
>>> from api import CrawlRequest, build_flow, execute_flow, set_module_handler
>>> from api.contracts import CrawlContext
>>> flow = build_flow(request, format_definition)
>>> context = CrawlContext(request=request, format_definition=format_definition)
>>> execute_flow(flow, context)
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping

from api.contracts import (
    CrawlContext,
    CrawlRequest,
    ContentType,
    InvalidFlowError,
    InvalidRequestError,
    MissingFlowError,
    OutputFormat,
    PackagingMode,
    SelectionMode,
    _normalize_url,
)
from api.raw_page import RawPageService, validate_fetch_configuration
from utils.fetcher import FetchMode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANDATORY_FLOW_STAGES = ("metadata", "volume_preparation", "chapter_crawl", "export")
"""Required, strictly-ordered pipeline stages shared by every stored flow."""

FLOW_ACTIONS = frozenset({"crawl_text_content", "download_image"})
"""Allowlisted actions a ``crawl_chapter`` step may combine."""

WHEN_ALLOWED_FIELDS = frozenset(
    {
        "content_type",
        "output_format",
        "selection",
        "packaging",
        "fetch_mode",
        "headless",
        "keep_logged_in",
        "max_workers",
        "sleep_ms",
    }
)
"""Request fields a ``when`` condition may branch on (no URLs or indexes)."""

WHEN_OPERATORS = frozenset({"eq", "neq", "in", "not_in"})
"""Allowlisted condition operators (equality/membership only)."""


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleSpec:
    """Registered specification of one allowlisted flow module."""

    name: str
    handler: Callable[[CrawlContext, dict], None]
    required: bool = False
    stage: str | None = None
    runtime_only: bool = False
    max_occurrences: int = 1
    allowed_params: frozenset[str] | None = None


_CANONICAL_MODULES: dict[str, dict[str, object]] = {
    "get_user_input": {
        "required": False,
        "stage": None,
        "runtime_only": True,
        "max_occurrences": 1,
        "allowed_params": frozenset(),
    },
    "get_metadata": {
        "required": True,
        "stage": "metadata",
        "runtime_only": False,
        "max_occurrences": 1,
        "allowed_params": frozenset({"expected_selector", "url"}),
    },
    "volumes_prepare": {
        "required": True,
        "stage": "volume_preparation",
        "runtime_only": False,
        "max_occurrences": 1,
        "allowed_params": frozenset({"expected_selector", "url"}),
    },
    "crawl_chapter": {
        "required": True,
        "stage": "chapter_crawl",
        "runtime_only": False,
        "max_occurrences": 1,
        "allowed_params": frozenset({"expected_selector", "url", "actions", "max_retries"}),
    },
    "create_ebook": {
        "required": True,
        "stage": "export",
        "runtime_only": False,
        "max_occurrences": 1,
        "allowed_params": frozenset(),
    },
}

REGISTRY: dict[str, ModuleSpec] = {}
"""Live module registry; ``build_flow`` rejects any unregistered module name."""


def _unimplemented_handler(name: str) -> Callable[[CrawlContext, dict], None]:
    def _handler(context: CrawlContext, params: dict) -> None:
        raise InvalidFlowError(
            f"module {name!r} has no crawl logic yet; its handler is registered "
            "by a later pipeline task."
        )

    return _handler


def _register_defaults() -> None:
    for name, config in _CANONICAL_MODULES.items():
        REGISTRY[name] = ModuleSpec(
            name=name,
            handler=_unimplemented_handler(name),
            required=bool(config["required"]),
            stage=config["stage"],  # type: ignore[arg-type]
            runtime_only=bool(config["runtime_only"]),
            max_occurrences=int(config["max_occurrences"]),
            allowed_params=config["allowed_params"],  # type: ignore[arg-type]
        )


def register_module(
    name: str,
    *,
    handler: Callable[[CrawlContext, dict], None],
    required: bool | None = None,
    stage: str | None = None,
    runtime_only: bool | None = None,
    max_occurrences: int | None = None,
    allowed_params: frozenset[str] | None = None,
) -> ModuleSpec:
    """Register (or upgrade) a flow module.

    Later pipeline tasks upgrade the four canonical modules with real handlers
    by passing ``handler`` (and optionally the full canonical spec) again.
    Structural attributes of a canonical module are immutable once registered.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("module name must be a non-empty string")
    if not callable(handler):
        raise ValueError(f"handler for module {name!r} must be callable")
    if max_occurrences is not None and max_occurrences < 1:
        raise ValueError("max_occurrences must be at least 1")

    canonical = _CANONICAL_MODULES.get(name)
    if canonical is not None:
        if stage is not None and stage != canonical["stage"]:
            raise ValueError(
                f"cannot change the stage of canonical module {name!r} "
                f"(must stay {canonical['stage']!r})."
            )
        if required is not None and required != bool(canonical["required"]):
            raise ValueError(f"cannot change the required flag of canonical module {name!r}.")
        if runtime_only is not None and runtime_only != bool(canonical["runtime_only"]):
            raise ValueError(
                f"cannot change the runtime flag of canonical module {name!r}."
            )
        if max_occurrences is not None and max_occurrences != int(canonical["max_occurrences"]):
            raise ValueError(
                f"cannot change the occurrence limit of canonical module {name!r}."
            )
        canonical_allowed = canonical["allowed_params"]
        if allowed_params is not None and frozenset(allowed_params) != canonical_allowed:
            raise ValueError(f"cannot change allowed params of canonical module {name!r}.")
        stage = canonical["stage"]  # type: ignore[assignment]
        required = bool(canonical["required"])
        runtime_only = bool(canonical["runtime_only"])
        max_occurrences = int(canonical["max_occurrences"])
        allowed_params = canonical_allowed  # type: ignore[assignment]

    if allowed_params is None:
        allowed_params = frozenset()
    if not isinstance(allowed_params, frozenset):
        allowed_params = frozenset(allowed_params)

    spec = ModuleSpec(
        name=name,
        handler=handler,
        required=bool(required),
        stage=stage,
        runtime_only=bool(runtime_only),
        max_occurrences=max_occurrences if max_occurrences is not None else 1,
        allowed_params=allowed_params,
    )
    REGISTRY[name] = spec
    return spec


def set_module_handler(
    name: str,
    handler: Callable[[CrawlContext, dict], None],
) -> ModuleSpec:
    """Replace only the handler of an already-registered module.

    Preserves the module's structural attributes (stage, required, runtime
    flag, occurrence limit, allowed params).  This is the recommended way for
    later pipeline tasks to plug in real ``get_metadata`` / ``volumes_prepare``
    / ``crawl_chapter`` / ``create_ebook`` implementations.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise ValueError(f"module {name!r} is not registered")
    if not callable(handler):
        raise ValueError(f"handler for module {name!r} must be callable")
    upgraded = ModuleSpec(
        name=spec.name,
        handler=handler,
        required=spec.required,
        stage=spec.stage,
        runtime_only=spec.runtime_only,
        max_occurrences=spec.max_occurrences,
        allowed_params=spec.allowed_params,
    )
    REGISTRY[name] = upgraded
    return upgraded


def get_module(name: str) -> ModuleSpec | None:
    """Return the registered :class:`ModuleSpec` for *name* or ``None``."""
    return REGISTRY.get(name)


def is_registered(name: str) -> bool:
    """Return whether *name* is an allowlisted, registered flow module."""
    return name in REGISTRY


def list_modules() -> tuple[str, ...]:
    """Return the registered module names in stable sorted order."""
    return tuple(sorted(REGISTRY))


def get_user_input_handler(context: CrawlContext, params: dict) -> None:
    """Runtime interactive step executed at the start of a TUI flow."""
    if context.log:
        context.log(f"Interactive crawl session initialized for {context.request.url}")


_register_defaults()
set_module_handler("get_user_input", get_user_input_handler)


# ---------------------------------------------------------------------------
# Flow value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowStep:
    """One validated flow step."""

    module: str
    params: Mapping[str, object] = field(default_factory=dict)
    when: Mapping[str, object] | None = None
    skipped: bool = False


@dataclass(frozen=True)
class Flow:
    """A validated, ordered flow ready for execution.

    ``steps`` lists every declared step; ``active_steps`` excludes steps whose
    ``when`` condition evaluated false against the request.
    """

    request: CrawlRequest
    interactive: bool
    format_definition: Mapping[str, object]
    steps: tuple[FlowStep, ...]
    active_steps: tuple[FlowStep, ...]


# ---------------------------------------------------------------------------
# Pure validation helpers
# ---------------------------------------------------------------------------


def _check_json_safe(value: object, label: str) -> None:
    """Reject any value that cannot travel through JSON (no code/objects)."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidFlowError(f"{label} contains a non-finite number (NaN/inf).")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidFlowError(f"{label} contains a non-string key {key!r}.")
            _check_json_safe(item, label)
        return
    if isinstance(value, list):
        for item in value:
            _check_json_safe(item, label)
        return
    raise InvalidFlowError(
        f"{label} must only contain JSON values; got {type(value).__name__}."
    )


def _freeze_json(value: object) -> object:
    """Return a deeply immutable JSON value for a validated flow."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    """Return an independent mutable JSON copy for a handler/service."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return copy.deepcopy(value)


def _validate_params(module: str, params: dict, spec: ModuleSpec, index: int) -> None:
    allowed = spec.allowed_params
    if allowed is None:
        return
    unknown = set(params) - set(allowed)
    if unknown:
        raise InvalidFlowError(
            f"flow[{index}] module {module!r} has unknown param(s): "
            f"{', '.join(sorted(unknown))}."
        )
    for key, value in params.items():
        if key == "expected_selector":
            if not isinstance(value, str):
                raise InvalidFlowError(
                    f"flow[{index}] module {module!r} param 'expected_selector' "
                    f"must be a string; got {type(value).__name__}."
                )
        elif key == "url":
            if not isinstance(value, str) or not value.strip():
                raise InvalidFlowError(
                    f"flow[{index}] module {module!r} param 'url' must be a "
                    "non-empty string URL."
                )
            try:
                _normalize_url(value, label=f"flow[{index}] module {module!r} param 'url'")
            except InvalidRequestError as exc:
                raise InvalidFlowError(str(exc)) from exc
        elif key == "actions":
            if not isinstance(value, list) or not value:
                raise InvalidFlowError(
                    f"flow[{index}] module {module!r} param 'actions' must be a "
                    "non-empty list of actions."
                )
            if not all(isinstance(action, str) for action in value):
                raise InvalidFlowError(
                    f"flow[{index}] module {module!r} param 'actions' must contain strings only."
                )
            if len(set(value)) != len(value):
                raise InvalidFlowError(
                    f"flow[{index}] module {module!r} param 'actions' repeats an "
                    "action; each action may appear only once."
                )
            for action in value:
                if action not in FLOW_ACTIONS:
                    raise InvalidFlowError(
                        f"flow[{index}] module {module!r} param 'actions' has "
                        f"unknown action {action!r}; allowed: "
                        f"{', '.join(sorted(FLOW_ACTIONS))}."
                    )
        elif key == "max_retries":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidFlowError(
                    f"flow[{index}] module {module!r} param 'max_retries' must be "
                    "a non-negative integer."
                )


_WHEN_ENUM_TYPES: dict[str, type[Enum]] = {
    "content_type": ContentType,
    "output_format": OutputFormat,
    "selection": SelectionMode,
    "packaging": PackagingMode,
}


def _validate_when_literal(field_name: str, literal: object, index: int) -> None:
    """Require condition literals to have the exact request-field type."""
    enum_cls = _WHEN_ENUM_TYPES.get(field_name)
    if enum_cls is not None:
        if not isinstance(literal, str) or literal not in {member.value for member in enum_cls}:
            raise InvalidFlowError(
                f"flow[{index}] 'when' value for {field_name!r} must be a valid {field_name} string."
            )
    elif field_name == "fetch_mode":
        if literal is not None and (not isinstance(literal, str) or not FetchMode.is_valid(literal)):
            raise InvalidFlowError(
                f"flow[{index}] 'when' value for 'fetch_mode' must be null or a valid fetch mode."
            )
    elif field_name in {"headless", "keep_logged_in"}:
        if not isinstance(literal, bool):
            raise InvalidFlowError(
                f"flow[{index}] 'when' value for {field_name!r} must be a boolean."
            )
    elif field_name in {"max_workers", "sleep_ms"}:
        if isinstance(literal, bool) or not isinstance(literal, int):
            raise InvalidFlowError(
                f"flow[{index}] 'when' value for {field_name!r} must be an integer."
            )


def _validate_when(when: dict, index: int) -> None:
    _check_json_safe(when, f"flow[{index}] 'when'")
    for field_name, condition in when.items():
        if field_name not in WHEN_ALLOWED_FIELDS:
            raise InvalidFlowError(
                f"flow[{index}] 'when' condition field {field_name!r} is not "
                "allowlisted; usable fields: "
                f"{', '.join(sorted(WHEN_ALLOWED_FIELDS))}."
            )
        if not isinstance(condition, dict) or len(condition) != 1:
            raise InvalidFlowError(
                f"flow[{index}] 'when' condition on {field_name!r} must be a "
                "single-operator object like {\"eq\": \"novel\"} (no nested "
                "expressions, functions, or imports)."
            )
        (operator, literal) = next(iter(condition.items()))
        if operator not in WHEN_OPERATORS:
            raise InvalidFlowError(
                f"flow[{index}] 'when' condition on {field_name!r} uses unknown "
                f"operator {operator!r}; allowed: "
                f"{', '.join(sorted(WHEN_OPERATORS))}."
            )
        if operator in ("in", "not_in") and not isinstance(literal, list):
            raise InvalidFlowError(
                f"flow[{index}] 'when' condition on {field_name!r} uses "
                f"{operator!r}, which requires a list value."
            )
        literals = literal if operator in ("in", "not_in") else [literal]
        if operator in ("in", "not_in") and not literals:
            raise InvalidFlowError(
                f"flow[{index}] 'when' condition on {field_name!r} must not use an empty membership list."
            )
        for item in literals:
            _validate_when_literal(field_name, item, index)


def _plain(value: object) -> object:
    """Normalize an enum request-field value to its JSON string form."""
    return value.value if isinstance(value, Enum) else value


def _conditions_match(when: Mapping[str, object], request: CrawlRequest) -> bool:
    """Evaluate all ``when`` conditions (AND) against the request."""
    for field_name, condition in when.items():
        left = _plain(getattr(request, field_name))  # type: ignore[arg-type]
        (operator, literal) = next(iter(condition.items()))  # type: ignore[union-attr]
        if operator == "eq":
            matched = left == literal
        elif operator == "neq":
            matched = left != literal
        elif operator == "in":
            matched = left in literal  # type: ignore[operator]
        elif operator == "not_in":
            matched = left not in literal  # type: ignore[operator]
        else:
            matched = False
        if not matched:
            return False
    return True


# ---------------------------------------------------------------------------
# Step building
# ---------------------------------------------------------------------------

_FLOW_STEP_FIELDS = frozenset({"module", "params", "when"})


def _build_step(entry: object, index: int) -> FlowStep:
    if not isinstance(entry, dict):
        raise InvalidFlowError(
            f"flow[{index}] must be a JSON step object; got {type(entry).__name__}."
        )
    unknown = set(entry) - _FLOW_STEP_FIELDS
    if unknown:
        raise InvalidFlowError(
            f"flow[{index}] has unknown step field(s): {', '.join(sorted(unknown))}; "
            "a step may only contain 'module', 'params', and 'when'."
        )
    module = entry.get("module")
    if not isinstance(module, str) or not module.strip():
        raise InvalidFlowError(
            f"flow[{index}] requires a non-empty 'module' string."
        )
    module = module.strip()
    spec = get_module(module)
    if spec is None:
        raise InvalidFlowError(
            f"flow[{index}] uses module {module!r}, which is not registered; "
            f"registered modules: {', '.join(list_modules())}."
        )

    params = entry.get("params", {})
    if not isinstance(params, dict):
        raise InvalidFlowError(
            f"flow[{index}] module {module!r} 'params' must be a JSON object."
        )
    _check_json_safe(params, f"flow[{index}] module {module!r} 'params'")
    _validate_params(module, params, spec, index)

    when = entry.get("when")
    if when is not None:
        if not isinstance(when, dict):
            raise InvalidFlowError(
                f"flow[{index}] module {module!r} 'when' must be a JSON object."
            )
        _validate_when(when, index)

    return FlowStep(
        module=module,
        params=_freeze_json(params),  # type: ignore[arg-type]
        when=_freeze_json(when) if when is not None else None,  # type: ignore[arg-type]
    )


def _validate_sequence(steps: list[FlowStep], interactive: bool) -> None:
    if not steps:
        raise InvalidFlowError("flow must contain at least one step.")

    # Per-module occurrence limits (runtime get_user_input: at most one, ...).
    counts: dict[str, int] = {}
    for step in steps:
        spec = get_module(step.module)
        if spec is None:
            raise InvalidFlowError(f"module {step.module!r} is not registered.")
        counts[step.module] = counts.get(step.module, 0) + 1
        if counts[step.module] > spec.max_occurrences:
            raise InvalidFlowError(
                f"module {step.module!r} is declared more than "
                f"{spec.max_occurrences} time(s)."
            )

    # Runtime-only modules (get_user_input) may only open an interactive flow.
    for index, step in enumerate(steps):
        spec = get_module(step.module)
        if spec.runtime_only:
            if not interactive:
                raise InvalidFlowError(
                    f"module {step.module!r} is runtime-only: it can only run as "
                    "the first step of an interactive TUI flow, never in a "
                    "stored flow."
                )
            if index != 0:
                raise InvalidFlowError(
                    f"runtime-only module {step.module!r} must be the first step."
                )

    seen: list[str] = []
    for step in steps:
        spec = get_module(step.module)
        if spec is None:
            raise InvalidFlowError(f"module {step.module!r} is not registered.")
        if spec.runtime_only:
            continue
        stage = spec.stage
        if stage is None or stage not in MANDATORY_FLOW_STAGES:
            raise InvalidFlowError(
                f"module {step.module!r} has no valid pipeline stage; only "
                "metadata, volume_preparation, chapter_crawl, and export steps "
                "can form a flow."
            )
        if stage in seen:
            raise InvalidFlowError(
                f"pipeline stage {stage!r} is declared more than once; each "
                "required stage must appear exactly once."
            )
        if seen and MANDATORY_FLOW_STAGES.index(stage) < MANDATORY_FLOW_STAGES.index(seen[-1]):
            raise InvalidFlowError(
                f"invalid flow ordering: stage {stage!r} must come after "
                f"{seen[-1]!r} (required order: metadata -> volume preparation "
                "-> chapter crawl -> export)."
            )
        seen.append(stage)

    if seen != list(MANDATORY_FLOW_STAGES):
        missing = [stage for stage in MANDATORY_FLOW_STAGES if stage not in seen]
        raise InvalidFlowError(
            f"flow is missing required stage(s): {', '.join(missing)}; a flow "
            "must contain metadata -> volume preparation -> chapter crawl -> "
            "export."
        )

    # A stored flow begins with get_metadata; an interactive flow begins with
    # the single runtime-only get_user_input step immediately before it.
    first_spec = get_module(steps[0].module)
    if not first_spec.runtime_only and steps[0].module != "get_metadata":
        raise InvalidFlowError("a stored flow must begin with the 'get_metadata' module.")

    if steps[-1].module != "create_ebook":
        raise InvalidFlowError("a flow must end with the 'create_ebook' module.")


def _validate_content_compatibility(request: CrawlRequest, steps: list[FlowStep]) -> None:
    """Require an executable action set for the requested content family."""
    chapter_step = next(step for step in steps if step.module == "crawl_chapter")
    actions = chapter_step.params.get("actions")
    if not isinstance(actions, tuple) or not actions:
        raise InvalidFlowError("'crawl_chapter' must declare a non-empty 'actions' list.")
    action_set = set(actions)
    if request.content_type is ContentType.NOVEL and "crawl_text_content" not in action_set:
        raise InvalidFlowError("novel flows require the 'crawl_text_content' chapter action.")
    if request.content_type in {ContentType.COMIC, ContentType.GALLERY} and "download_image" not in action_set:
        raise InvalidFlowError(
            f"{request.content_type.value} flows require the 'download_image' chapter action."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_flow(
    request: object,
    format_definition: object,
    *,
    interactive: bool = False,
) -> Flow:
    """Build a validated :class:`Flow` or raise the deterministic error.

    * ``request`` may be a :class:`CrawlRequest` or a JSON dict accepted by
      ``CrawlRequest.from_dict``.
    * A missing/absent ``flow`` key (legacy format) raises
      :class:`MissingFlowError`; every structural/schema problem raises
      :class:`InvalidFlowError`.
    * Validation is pure: it performs no network access and creates no
      directories.  A required stage may not be conditionally skipped.

    Parameters
    ----------
    interactive : bool, optional
        Prepend the runtime-only ``get_user_input`` module after validating the
        stored flow.  Stored site flows must never contain it.
    """
    if not isinstance(request, CrawlRequest):
        request = CrawlRequest.from_dict(request)

    if not isinstance(format_definition, dict):
        raise MissingFlowError(
            "Site format has no 'flow' key (legacy format). A flow is required "
            "for the breaking cutover; update the format JSON to add a 'flow' "
            "array before crawling."
        )
    raw_flow = format_definition.get("flow")
    if raw_flow is None:
        raise MissingFlowError(
            "Site format has no 'flow' key (legacy format). A flow is required "
            "for the breaking cutover; update the format JSON to add a 'flow' "
            "array before crawling."
        )
    if not isinstance(raw_flow, list):
        raise InvalidFlowError(
            "'flow' must be an ordered JSON list of step objects; "
            f"got {type(raw_flow).__name__}."
        )

    raw_content_type = format_definition.get("content_type")
    if not isinstance(raw_content_type, str):
        raise InvalidFlowError("format_definition requires a string 'content_type'.")
    try:
        format_content_type = ContentType(raw_content_type.strip().lower())
    except ValueError:
        raise InvalidFlowError("format_definition 'content_type' must be novel, comic, or gallery.") from None
    if format_content_type is not request.content_type:
        raise InvalidFlowError(
            f"format content_type {format_content_type.value!r} does not match request "
            f"content_type {request.content_type.value!r}."
        )

    steps = [_build_step(entry, index) for index, entry in enumerate(raw_flow)]
    _validate_sequence(steps, interactive=False)
    _validate_content_compatibility(request, steps)
    validate_fetch_configuration(request, format_definition)

    evaluated = tuple(
        replace(step, skipped=not _conditions_match(step.when, request)) if step.when else step
        for step in steps
    )
    for step in evaluated:
        spec = get_module(step.module)
        if step.skipped and spec is not None and spec.required:
            raise InvalidFlowError(
                f"required module {step.module!r} is conditionally skipped for this request."
            )

    runtime_steps: tuple[FlowStep, ...] = ()
    if interactive:
        runtime_steps = (FlowStep(module="get_user_input", params=MappingProxyType({})),)
    all_steps = runtime_steps + evaluated
    active = tuple(step for step in all_steps if not step.skipped)
    return Flow(
        request=request,
        interactive=interactive,
        format_definition=_freeze_json(format_definition),  # type: ignore[arg-type]
        steps=all_steps,
        active_steps=active,
    )


def execute_flow(flow: Flow, context: CrawlContext) -> None:
    """Run every active step of *flow* through the module registry.

    The engine provisions ``context.raw_page`` (a long-lived raw-page service
    reusing one session/browser across steps) before the first step and always
    closes it in a ``finally`` block — after success, failure, or interruption
    (including ``KeyboardInterrupt``).
    """
    if not isinstance(flow, Flow):
        raise TypeError(f"execute_flow requires a Flow; got {type(flow).__name__}.")
    if not isinstance(context, CrawlContext):
        raise TypeError(
            f"execute_flow requires a CrawlContext; got {type(context).__name__}."
        )

    if context.request != flow.request:
        raise InvalidFlowError(
            "CrawlContext request does not match the request used to validate the flow."
        )

    validated_format = _thaw_json(flow.format_definition)
    if not isinstance(validated_format, dict):  # Defensive: Flow only stores JSON objects.
        raise InvalidFlowError("validated flow has an invalid format definition.")
    if context.format_definition and context.format_definition != validated_format:
        raise InvalidFlowError(
            "CrawlContext format_definition does not match the definition used to validate the flow."
        )
    context.format_definition = validated_format
    from utils.worker_config import GlobalPacer

    context.pacer = GlobalPacer(context.request.sleep_ms / 1000).acquire
    context.raw_page = RawPageService(
        context.request,
        context.format_definition,
        before_request=context.pacer,
    )
    try:
        for step in flow.active_steps:
            context.current_stage = step.module
            spec = get_module(step.module)
            if spec is None:
                raise InvalidFlowError(
                    f"module {step.module!r} is no longer registered and cannot execute."
                )
            params = _thaw_json(step.params)
            if not isinstance(params, dict):  # Defensive: FlowStep always stores an object.
                raise InvalidFlowError(f"module {step.module!r} has invalid validated params.")
            context.current_stage = spec.stage
            if context.log:
                context.log(f"Stage started: {step.module}")
            spec.handler(context, params)
            if context.log:
                context.log(f"Stage completed: {step.module}")
        context.current_stage = None
    finally:
        service = context.raw_page
        context.raw_page = None
        context.pacer = None
        if service is not None:
            service.close()


__all__ = [
    "FLOW_ACTIONS",
    "Flow",
    "FlowStep",
    "MANDATORY_FLOW_STAGES",
    "ModuleSpec",
    "REGISTRY",
    "WHEN_ALLOWED_FIELDS",
    "WHEN_OPERATORS",
    "build_flow",
    "execute_flow",
    "get_module",
    "is_registered",
    "list_modules",
    "register_module",
    "set_module_handler",
]
