"""Typed, storage-neutral pipeline contracts and execution engine."""

from __future__ import annotations

import csv
import inspect
import json
import traceback as traceback_module
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar, Generic, Protocol, TypeVar, cast


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


class ColName(str, Enum):
    """Canonical column names used by result-table adapters."""

    DATASET_ID = "dataset_id"
    STEP_NAME = "step_name"
    STATUS = "status"
    SUCCESS = "success"
    DESCRIPTION = "description"
    STARTED_AT = "started_at"
    ENDED_AT = "ended_at"
    DURATION_SECONDS = "duration_seconds"
    ERROR_TYPE = "error_type"
    ERROR_MESSAGE = "error_message"
    ARTIFACTS = "artifacts"


@dataclass(frozen=True)
class ArtifactRef:
    """A storage-neutral reference to an artifact produced by a step."""

    path: Path | str
    kind: str = "file"
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    media_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class PipelineError:
    type: str
    message: str
    traceback: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_exception(cls, error: BaseException) -> PipelineError:
        return cls(
            type=type(error).__name__,
            message=str(error),
            traceback="".join(
                traceback_module.format_exception(type(error), error, error.__traceback__)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class PipelineStepValidatorResult:
    success: bool
    description: str = ""
    details: Any = None

    def __bool__(self) -> bool:
        return self.success

    @property
    def msg(self) -> str:
        """Compatibility alias used by older class-based validators."""
        return self.description

    @classmethod
    def normalize(cls, value: Any) -> PipelineStepValidatorResult:
        """Normalize legacy Boolean/list validator returns into one result."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls(True)
        if isinstance(value, bool):
            return cls(value)
        if isinstance(value, (list, tuple)):
            normalized = [cls.normalize(item) for item in value]
            failures = [item for item in normalized if not item.success]
            return cls(
                not failures,
                "; ".join(item.description for item in failures if item.description),
                [item.to_dict() for item in normalized],
            )
        return cls(bool(value), details=value)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


class PipelineContext(MutableMapping[str, Any]):
    """Instance-owned mutable parameter context shared by one pipeline."""

    def __init__(self, params: Mapping[str, Any] | None = None, **values: Any) -> None:
        self.params: dict[str, Any] = dict(params or {})
        self.params.update(values)
        self.step_name = ""
        self.validation_success: bool | None = None
        self.result: Any = None
        self.result_persisted = False
        self.colnames: Any = None
        self.logfolder: Path | None = None

    def __getitem__(self, key: str) -> Any:
        return self.params[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.params[key] = value

    def __delitem__(self, key: str) -> None:
        del self.params[key]

    def __iter__(self):
        return iter(self.params)

    def __len__(self) -> int:
        return len(self.params)

    def copy(self) -> PipelineContext:
        return type(self)(self.params)

    def init_params(self, params: Mapping[str, Any]) -> None:
        self.params = {**params, **self.params}

    def reset_params(self) -> None:
        self.params.clear()

    def read_paramfile(self, path: str | Path) -> dict[str, Any]:
        """Read simple ``key=value`` parameters without owning config parsing."""
        values: dict[str, Any] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
        self.params.update(values)
        return values

    def to_dict(self) -> dict[str, Any]:
        return dict(self.params)


class PipelineStepValidatorBase:
    """Base class for typed validators."""

    name: str = ""
    description: str = ""
    run_once: bool = False
    run_after: bool = False

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        run_once: bool | None = None,
    ) -> None:
        self.name = name or self.name or type(self).__name__
        if description is not None:
            self.description = description
        if run_once is not None:
            self.run_once = run_once

    def validate(self, **params: Any) -> Any:  # pragma: no cover - contract method
        raise NotImplementedError

    def __call__(self, **params: Any) -> Any:
        return self.validate(**params)

    def run(self, **params: Any) -> Any:
        return self.validate(**params)


class PipelineStepBase:
    """Base class for typed pipeline steps."""

    name: str = ""
    description: str = ""
    before: Sequence[PipelineStepValidatorBase | Callable[..., Any]] = ()
    after: Sequence[PipelineStepValidatorBase | Callable[..., Any]] = ()
    validate_by: Sequence[type[PipelineStepValidatorBase] | PipelineStepValidatorBase] = ()

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        before: Sequence[PipelineStepValidatorBase | Callable[..., Any]] | None = None,
        after: Sequence[PipelineStepValidatorBase | Callable[..., Any]] | None = None,
    ) -> None:
        self.name = name or self.name or type(self).__name__
        if description is not None:
            self.description = description
        self.before = tuple(self.before if before is None else before)
        self.after = tuple(self.after if after is None else after)
        if self.validate_by and before is None and after is None:
            validators = [item() if isinstance(item, type) else item for item in self.validate_by]
            self.before = tuple(item for item in validators if not getattr(item, "run_after", False))
            self.after = tuple(item for item in validators if getattr(item, "run_after", False))

    def execute(self, **params: Any) -> Any:  # pragma: no cover - contract method
        raise NotImplementedError

    def __call__(self, **params: Any) -> Any:
        return self.execute(**params)

    def run(self, **params: Any) -> Any:
        return self.execute(**params)


class FunctionPipelineStep(PipelineStepBase):
    """Boundary adapter from a decorated function to a typed step."""

    def __init__(
        self,
        function: Callable[..., Any],
        *,
        name: str | None = None,
        description: str = "",
        before: Sequence[PipelineStepValidatorBase | Callable[..., Any]] = (),
        after: Sequence[PipelineStepValidatorBase | Callable[..., Any]] = (),
    ) -> None:
        super().__init__(
            name=name or function.__name__,
            description=description,
            before=before,
            after=after,
        )
        self.function = function

    def execute(self, **params: Any) -> Any:
        return self.function(**params)


class FunctionPipelineStepValidator(PipelineStepValidatorBase):
    """Boundary adapter from a decorated function to a typed validator."""

    def __init__(
        self,
        function: Callable[..., Any],
        *,
        name: str | None = None,
        description: str = "",
        run_once: bool = False,
    ) -> None:
        super().__init__(
            name=name or function.__name__, description=description, run_once=run_once
        )
        self.function = function

    def validate(self, **params: Any) -> Any:
        return self.function(**params)


@dataclass
class StepResult:
    dataset_id: str
    step_name: str
    status: str
    value: Any = None
    description: str = ""
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime = field(default_factory=_now)
    duration_seconds: float = 0.0
    error: PipelineError | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    validation: list[PipelineStepValidatorResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "succeeded"

    @property
    def name(self) -> str:
        return self.step_name

    @property
    def success_count(self) -> int:
        return int(self.success)

    @property
    def failed_count(self) -> int:
        return int(not self.success and self.status != "skipped")

    @property
    def start_stamp(self) -> datetime:
        return self.started_at

    @property
    def end_stamp(self) -> datetime:
        return self.ended_at

    @property
    def detail(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "error": self.error.to_dict() if self.error else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    @property
    def desc(self) -> list[str]:
        return [self.description] if self.description else []

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class DatasetResult:
    dataset_id: str
    steps: list[StepResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime = field(default_factory=_now)
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return bool(self.steps) and all(step.success for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class BatchResult:
    datasets: list[DatasetResult] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime = field(default_factory=_now)
    duration_seconds: float = 0.0
    artifacts: list[ArtifactRef] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.datasets) and all(dataset.success for dataset in self.datasets)

    @property
    def step_results(self) -> list[StepResult]:
        return [step for dataset in self.datasets for step in dataset.steps]

    @property
    def success_count(self) -> int:
        return sum(dataset.success for dataset in self.datasets)

    @property
    def failed_count(self) -> int:
        return len(self.datasets) - self.success_count

    def to_dict(self) -> dict[str, Any]:
        data = _serialize(self)
        data["success"] = self.success
        return data

    @classmethod
    def from_step_results(cls, results: Iterable[StepResult], **kwargs: Any) -> BatchResult:
        grouped: dict[str, list[StepResult]] = defaultdict(list)
        for result in results:
            grouped[result.dataset_id].append(result)
        datasets = [DatasetResult(dataset_id=key, steps=value) for key, value in grouped.items()]
        return cls(datasets=datasets, **kwargs)


@dataclass(frozen=True)
class ExecutionEvent:
    run_id: str
    timestamp: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class RunStarted(ExecutionEvent):
    dataset_count: int = 0


@dataclass(frozen=True)
class DatasetStarted(ExecutionEvent):
    dataset_id: str = ""


@dataclass(frozen=True)
class StepStarted(ExecutionEvent):
    dataset_id: str = ""
    step_name: str = ""


@dataclass(frozen=True)
class StepSucceeded(ExecutionEvent):
    dataset_id: str = ""
    step_name: str = ""
    result: StepResult | None = None


@dataclass(frozen=True)
class StepFailed(ExecutionEvent):
    dataset_id: str = ""
    step_name: str = ""
    result: StepResult | None = None


@dataclass(frozen=True)
class StepSkipped(ExecutionEvent):
    dataset_id: str = ""
    step_name: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DatasetFinished(ExecutionEvent):
    dataset_id: str = ""
    success: bool = False


@dataclass(frozen=True)
class RunFinished(ExecutionEvent):
    success: bool = False
    result: BatchResult | None = None


class EventSink(Protocol):
    def __call__(self, event: ExecutionEvent) -> None: ...


class CrashAdapter(Protocol):
    def write(self, snapshot: Mapping[str, Any]) -> ArtifactRef | None: ...


class BatchResultAdapter(Protocol):
    def write(self, result: BatchResult) -> ArtifactRef | None: ...


class CrashSnapshotAdapter:
    """Write the most recent structured failure snapshot atomically as JSON."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, snapshot: Mapping[str, Any]) -> ArtifactRef:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_serialize(snapshot), indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.path)
        return ArtifactRef(self.path, kind="crash-snapshot")

    save = write


class ResultCSVAdapter:
    """Serialize one row per dataset-step result to CSV."""

    fieldnames: ClassVar[list[str]] = [item.value for item in ColName]

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, result: BatchResult) -> ArtifactRef:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.fieldnames)
            writer.writeheader()
            for item in result.step_results:
                writer.writerow(
                    {
                        ColName.DATASET_ID.value: item.dataset_id,
                        ColName.STEP_NAME.value: item.step_name,
                        ColName.STATUS.value: item.status,
                        ColName.SUCCESS.value: item.success,
                        ColName.DESCRIPTION.value: item.description,
                        ColName.STARTED_AT.value: item.started_at.isoformat(),
                        ColName.ENDED_AT.value: item.ended_at.isoformat(),
                        ColName.DURATION_SECONDS.value: item.duration_seconds,
                        ColName.ERROR_TYPE.value: item.error.type if item.error else "",
                        ColName.ERROR_MESSAGE.value: item.error.message if item.error else "",
                        ColName.ARTIFACTS.value: json.dumps(
                            [artifact.to_dict() for artifact in item.artifacts], sort_keys=True
                        ),
                    }
                )
        temporary.replace(self.path)
        return ArtifactRef(self.path, kind="result-csv")

    save = write


def write_crash_snapshot(path: str | Path, snapshot: Mapping[str, Any]) -> ArtifactRef:
    """Functional crash-adapter entry point for compatibility consumers."""
    adapter = CrashSnapshotAdapter(path)
    adapter.write(snapshot)
    return ArtifactRef(adapter.path, kind="crash-snapshot")


def append_step_result_csv(result: StepResult, path: str | Path) -> ArtifactRef:
    """Append one canonical step result to a result CSV."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_header = not destination.exists() or destination.stat().st_size == 0
    adapter = ResultCSVAdapter(destination)
    with destination.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=adapter.fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                ColName.DATASET_ID.value: result.dataset_id,
                ColName.STEP_NAME.value: result.step_name,
                ColName.STATUS.value: result.status,
                ColName.SUCCESS.value: result.success,
                ColName.DESCRIPTION.value: result.description,
                ColName.STARTED_AT.value: result.started_at.isoformat(),
                ColName.ENDED_AT.value: result.ended_at.isoformat(),
                ColName.DURATION_SECONDS.value: result.duration_seconds,
                ColName.ERROR_TYPE.value: result.error.type if result.error else "",
                ColName.ERROR_MESSAGE.value: result.error.message if result.error else "",
                ColName.ARTIFACTS.value: json.dumps(
                    [artifact.to_dict() for artifact in result.artifacts], sort_keys=True
                ),
            }
        )
    return ArtifactRef(destination, kind="result-csv")


DatasetT = TypeVar("DatasetT", bound=Mapping[str, Any])


class PipelineCore(Generic[DatasetT]):
    """Canonical ordered execution loop for typed and adapted steps."""

    def __init__(
        self,
        steps: Iterable[PipelineStepBase] | Mapping[str, Any] = (),
        pipeline_steps: Iterable[PipelineStepBase] | None = None,
        provided_pipe_params: Mapping[str, Any] | None = None,
        *,
        context: PipelineContext | Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
        event_sink: EventSink | Any | None = None,
        crash_adapter: CrashAdapter | Any | None = None,
        result_adapter: BatchResultAdapter | Any | None = None,
    ) -> None:
        if isinstance(steps, Mapping):
            if config is None:
                config = cast(Mapping[str, Any], steps)
            selected_steps = pipeline_steps or ()
        elif pipeline_steps is not None:
            raise TypeError("pipeline_steps is only valid when the first argument is config")
        else:
            selected_steps = steps
        self.steps: list[PipelineStepBase] = []
        self._steps_by_name: dict[str, PipelineStepBase] = {}
        self.context = context if isinstance(context, PipelineContext) else PipelineContext(context)
        self.config = self._merge_config(config)
        self.event_sink = event_sink
        self.crash_adapter = crash_adapter
        self.result_adapter = result_adapter
        self.provided_pipe_params = dict(provided_pipe_params or {})
        self._validators_run_once: set[int] = set()
        for step in cast(Iterable[PipelineStepBase], selected_steps):
            self.register_step(step)

    @staticmethod
    def _merge_config(
        config: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        if config is None:
            return {}
        if isinstance(config, Mapping):
            return dict(cast(Mapping[str, Any], config))
        merged: dict[str, Any] = {}
        for layer in config:
            merged.update(layer)
        return merged

    def register_step(
        self, step: PipelineStepBase | type[PipelineStepBase]
    ) -> PipelineStepBase:
        if isinstance(step, type) and issubclass(step, PipelineStepBase):
            step = step()
        if not isinstance(step, PipelineStepBase):
            raise TypeError("steps must be PipelineStepBase instances")
        if step.name in self._steps_by_name:
            raise ValueError(f"Step with name {step.name!r} already registered")
        self.steps.append(step)
        self._steps_by_name[step.name] = step
        return step

    add_step = register_step

    def register_steps(self) -> list[PipelineStepBase]:
        """Compatibility no-op: construction already registers in order."""
        return list(self.steps)

    def step_names(self) -> list[str]:
        return [step.name for step in self.steps]

    def steps_from(self, step_name: str) -> list[str]:
        names = self.step_names()
        return names[names.index(step_name) :]

    def filter_steps(self, *step_names: str) -> PipelineCore[DatasetT]:
        wanted = set(step_names)
        unknown = wanted.difference(self._steps_by_name)
        if unknown:
            raise KeyError(f"Unknown pipeline steps: {', '.join(sorted(unknown))}")
        self.steps = [step for step in self.steps if step.name in wanted]
        self._steps_by_name = {step.name: step for step in self.steps}
        return self

    def get_kwargs(self, step: PipelineStepBase) -> dict[str, Any]:
        return self.resolve_parameters(step, self.provided_pipe_params)

    @classmethod
    def from_legacy_registries(
        cls,
        registered_steps: Mapping[str, Mapping[str, Any]],
        validate_before: Mapping[str, Mapping[str, Any]] | None = None,
        validate_after: Mapping[str, Mapping[str, Any]] | None = None,
        validators: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        sequence: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> PipelineCore[Any]:
        """Adapt legacy decorator registries without making them engine state."""
        validate_before = validate_before or {}
        validate_after = validate_after or {}
        validators = validators or {}

        def adapt_validator(function: Callable[..., Any]) -> FunctionPipelineStepValidator:
            metadata = validators.get(function.__name__, {})
            return FunctionPipelineStepValidator(
                function,
                description=str(metadata.get("desc", "")),
                run_once=bool(metadata.get("run_once", False)),
            )

        names = list(sequence) if sequence is not None else list(registered_steps)
        steps: list[FunctionPipelineStep] = []
        for name in names:
            metadata = registered_steps[name]
            before = [
                adapt_validator(function)
                for function in validate_before.get(name, {}).get("functions", ())
            ]
            after = [
                adapt_validator(function)
                for function in validate_after.get(name, {}).get("functions", ())
            ]
            steps.append(
                FunctionPipelineStep(
                    metadata["function"],
                    name=name,
                    description=str(metadata.get("desc", "")),
                    before=before,
                    after=after,
                )
            )
        return cls(steps, **kwargs)

    def resolve_parameters(
        self,
        target: PipelineStepBase | PipelineStepValidatorBase | Callable[..., Any],
        explicit: Mapping[str, Any] | None = None,
        *,
        dataset: Mapping[str, Any] | None = None,
        qualified_name: str | None = None,
        callable_: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve exact precedence without eagerly materializing callable defaults."""
        explicit = dict(explicit or {})
        context = dict(self.context)
        context.update(dataset or {})
        name = qualified_name or getattr(target, "name", getattr(target, "__name__", ""))
        function = callable_ or self._callable_for(target)
        signature = inspect.signature(function)
        nested = explicit.get(name, {})
        nested = nested if isinstance(nested, Mapping) else {}
        qualified: dict[str, Any] = dict(nested)
        prefix = f"{name}."
        qualified.update(
            {key[len(prefix) :]: value for key, value in explicit.items() if key.startswith(prefix)}
        )
        candidates = dict(self.config)
        candidates.update(context)
        candidates.update(
            {
                key: value
                for key, value in explicit.items()
                if "." not in key and key != name and key not in self._steps_by_name
            }
        )
        candidates.update(qualified)

        resolved: dict[str, Any] = {}
        accepts_kwargs = False
        missing: list[str] = []
        for parameter in signature.parameters.values():
            if parameter.name in {"self", "cls"}:
                continue
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                accepts_kwargs = True
                continue
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                continue
            if parameter.name == "context" and parameter.name not in candidates:
                resolved[parameter.name] = PipelineContext(context)
            elif parameter.name in candidates and not (
                candidates[parameter.name] is None
                and parameter.default is not inspect.Parameter.empty
            ):
                resolved[parameter.name] = candidates[parameter.name]
            elif parameter.default is inspect.Parameter.empty:
                missing.append(parameter.name)
        if missing:
            raise TypeError(f"Missing required parameters: {', '.join(missing)}")
        if accepts_kwargs:
            for key, value in candidates.items():
                resolved.setdefault(key, value)
        return resolved

    @staticmethod
    def _callable_for(target: Any) -> Callable[..., Any]:
        if isinstance(target, PipelineStepBase):
            function = (
                target.run
                if type(target).execute is PipelineStepBase.execute
                and type(target).run is not PipelineStepBase.run
                else target.execute
            )
            # Function adapters conventionally expose ``execute(**params)`` and
            # retain the wrapped callable as ``function``. Resolve against the
            # wrapped signature so unrelated context keys are not forwarded.
            wrapped = getattr(target, "function", None)
            if callable(wrapped) and all(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in inspect.signature(function).parameters.values()
            ):
                return wrapped
            return function
        if isinstance(target, PipelineStepValidatorBase):
            function = (
                target.run
                if type(target).validate is PipelineStepValidatorBase.validate
                and type(target).run is not PipelineStepValidatorBase.run
                else target.validate
            )
            wrapped = getattr(target, "function", None)
            if callable(wrapped) and all(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in inspect.signature(function).parameters.values()
            ):
                return wrapped
            return function
        return target

    def run(
        self,
        datasets: Iterable[DatasetT] | DatasetT | None = None,
        *,
        params: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> BatchResult:
        dataset_items = self._normalize_datasets(datasets)
        run_id = run_id or uuid.uuid4().hex
        run_started = _now()
        run_clock = perf_counter()
        self._validators_run_once.clear()
        self._emit(RunStarted(run_id=run_id, dataset_count=len(dataset_items)))
        dataset_results: list[DatasetResult] = []
        for index, dataset in enumerate(dataset_items):
            dataset_results.append(self._run_dataset(run_id, index, dataset, params or {}))
        run_ended = _now()
        result = BatchResult(
            datasets=dataset_results,
            run_id=run_id,
            started_at=run_started,
            ended_at=run_ended,
            duration_seconds=perf_counter() - run_clock,
        )
        if self.result_adapter is not None:
            artifact = self._write_adapter(self.result_adapter, result)
            if isinstance(artifact, ArtifactRef):
                result.artifacts.append(artifact)
        self._emit(RunFinished(run_id=run_id, success=result.success, result=result))
        return result

    def execute(
        self,
        loglevel: str = "INFO",
        *,
        datasets: Iterable[DatasetT] | DatasetT | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> BatchResult:
        """Class-based compatibility entry point; logging remains caller-owned."""
        del loglevel
        merged = dict(self.provided_pipe_params)
        merged.update(params or {})
        return self.run(datasets, params=merged)

    def validate(
        self,
        validators: Sequence[PipelineStepValidatorBase | Callable[..., Any]],
        *,
        qualified_name: str = "validation",
        dataset: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        result: Any = inspect.Parameter.empty,
    ) -> list[PipelineStepValidatorResult]:
        """Run validators through the engine's canonical normalization path."""
        placeholder = FunctionPipelineStep(lambda: None, name=qualified_name)
        return self._run_validators(
            validators,
            placeholder,
            dataset or {},
            params or {},
            result=result,
        )

    @staticmethod
    def _normalize_datasets(
        datasets: Iterable[DatasetT] | DatasetT | None,
    ) -> list[Mapping[str, Any]]:
        if datasets is None:
            return [{}]
        if isinstance(datasets, Mapping):
            return [dict(cast(Mapping[str, Any], datasets))]
        return [dict(cast(Mapping[str, Any], dataset)) for dataset in datasets]

    def _run_dataset(
        self,
        run_id: str,
        index: int,
        dataset: Mapping[str, Any],
        explicit: Mapping[str, Any],
    ) -> DatasetResult:
        dataset_id = str(
            dataset.get(ColName.DATASET_ID.value, dataset.get("id", dataset.get("name", index)))
        )
        started = _now()
        clock = perf_counter()
        self._emit(DatasetStarted(run_id=run_id, dataset_id=dataset_id))
        results: list[StepResult] = []
        failed = False
        for step in self.steps:
            if failed:
                now = _now()
                skipped = StepResult(
                    dataset_id=dataset_id,
                    step_name=step.name,
                    status="skipped",
                    description="skipped after an earlier step failed",
                    started_at=now,
                    ended_at=now,
                )
                results.append(skipped)
                self._emit(
                    StepSkipped(
                        run_id=run_id,
                        dataset_id=dataset_id,
                        step_name=step.name,
                        reason=skipped.description,
                    )
                )
                continue
            step_result = self._run_step(run_id, dataset_id, dataset, step, explicit)
            results.append(step_result)
            failed = not step_result.success
        ended = _now()
        dataset_result = DatasetResult(
            dataset_id=dataset_id,
            steps=results,
            started_at=started,
            ended_at=ended,
            duration_seconds=perf_counter() - clock,
        )
        self._emit(
            DatasetFinished(run_id=run_id, dataset_id=dataset_id, success=dataset_result.success)
        )
        return dataset_result

    def _run_step(
        self,
        run_id: str,
        dataset_id: str,
        dataset: Mapping[str, Any],
        step: PipelineStepBase,
        explicit: Mapping[str, Any],
    ) -> StepResult:
        started = _now()
        clock = perf_counter()
        self._emit(StepStarted(run_id=run_id, dataset_id=dataset_id, step_name=step.name))
        validation_results: list[PipelineStepValidatorResult] = []
        try:
            before = self._run_validators(step.before, step, dataset, explicit)
            validation_results.extend(before)
            rejected = next((item for item in before if not item.success), None)
            if rejected is not None:
                error = PipelineError(
                    type="ValidationError",
                    message=rejected.description or "pre-step validation failed",
                    details={"phase": "before", "results": [item.to_dict() for item in before]},
                )
                return self._failed_step(
                    run_id, dataset_id, step, started, clock, error, validation_results
                )
            parameters = self.resolve_parameters(step, explicit, dataset=dataset)
            returned = self._callable_for(step)(**parameters)
            value, artifacts, returned_result = self._normalize_step_return(returned)
            after = self._run_validators(step.after, step, dataset, explicit, result=value)
            validation_results.extend(after)
            rejected = next((item for item in after if not item.success), None)
            if rejected is not None:
                error = PipelineError(
                    type="ValidationError",
                    message=rejected.description or "post-step validation failed",
                    details={"phase": "after", "results": [item.to_dict() for item in after]},
                )
                return self._failed_step(
                    run_id,
                    dataset_id,
                    step,
                    started,
                    clock,
                    error,
                    validation_results,
                    value=value,
                    artifacts=artifacts,
                )
            ended = _now()
            result = returned_result or StepResult(dataset_id, step.name, "succeeded")
            result.dataset_id = dataset_id
            result.step_name = step.name
            result.status = "succeeded"
            result.value = value
            result.description = result.description or step.description
            result.started_at = started
            result.ended_at = ended
            result.duration_seconds = perf_counter() - clock
            result.error = None
            result.artifacts = artifacts
            result.validation = validation_results
            self._emit(
                StepSucceeded(
                    run_id=run_id, dataset_id=dataset_id, step_name=step.name, result=result
                )
            )
            return result
        except Exception as error:  # noqa: BLE001 - step failures are result data
            return self._failed_step(
                run_id,
                dataset_id,
                step,
                started,
                clock,
                PipelineError.from_exception(error),
                validation_results,
            )

    def _run_validators(
        self,
        validators: Sequence[PipelineStepValidatorBase | Callable[..., Any]],
        step: PipelineStepBase,
        dataset: Mapping[str, Any],
        explicit: Mapping[str, Any],
        *,
        result: Any = inspect.Parameter.empty,
    ) -> list[PipelineStepValidatorResult]:
        normalized: list[PipelineStepValidatorResult] = []
        for validator in validators:
            run_once = bool(getattr(validator, "run_once", False))
            identity = id(getattr(validator, "function", validator))
            if run_once and identity in self._validators_run_once:
                continue
            function = self._callable_for(validator)
            validator_dataset = dict(dataset)
            if result is not inspect.Parameter.empty:
                validator_dataset["result"] = result
            parameters = self.resolve_parameters(
                validator,
                explicit,
                dataset=validator_dataset,
                qualified_name=step.name,
                callable_=function,
            )
            raw = function(**parameters)
            validation_result = PipelineStepValidatorResult.normalize(raw)
            normalized.append(validation_result)
            if run_once:
                self._validators_run_once.add(identity)
            if not validation_result.success:
                break
        return normalized

    @staticmethod
    def _normalize_step_return(
        returned: Any,
    ) -> tuple[Any, list[ArtifactRef], StepResult | None]:
        returned_result = returned if isinstance(returned, StepResult) else None
        value = returned.value if returned_result else returned
        artifacts = list(returned.artifacts) if returned_result else []
        if isinstance(value, ArtifactRef):
            artifacts.append(value)
            value = None
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, ArtifactRef) for item in value
        ):
            artifacts.extend(value)
            value = None
        return value, artifacts, returned_result

    def _failed_step(
        self,
        run_id: str,
        dataset_id: str,
        step: PipelineStepBase,
        started: datetime,
        clock: float,
        error: PipelineError,
        validation: list[PipelineStepValidatorResult],
        *,
        value: Any = None,
        artifacts: list[ArtifactRef] | None = None,
    ) -> StepResult:
        result = StepResult(
            dataset_id=dataset_id,
            step_name=step.name,
            status="failed",
            value=value,
            description=step.description,
            started_at=started,
            ended_at=_now(),
            duration_seconds=perf_counter() - clock,
            error=error,
            artifacts=list(artifacts or []),
            validation=validation,
        )
        self._emit(
            StepFailed(run_id=run_id, dataset_id=dataset_id, step_name=step.name, result=result)
        )
        if self.crash_adapter is not None:
            artifact = self._write_adapter(
                self.crash_adapter,
                {
                    "run_id": run_id,
                    "dataset_id": dataset_id,
                    "step_name": step.name,
                    "context": dict(self.context),
                    "error": error.to_dict(),
                    "result": result.to_dict(),
                },
            )
            if isinstance(artifact, ArtifactRef):
                result.artifacts.append(artifact)
        return result

    def _emit(self, event: ExecutionEvent) -> None:
        if self.event_sink is None:
            return
        if callable(self.event_sink):
            self.event_sink(event)
        elif hasattr(self.event_sink, "emit"):
            self.event_sink.emit(event)
        else:
            raise TypeError("event_sink must be callable or provide emit(event)")

    @staticmethod
    def _write_adapter(adapter: Any, value: Any) -> Any:
        if hasattr(adapter, "write"):
            return adapter.write(value)
        elif hasattr(adapter, "save"):
            return adapter.save(value)
        elif callable(adapter):
            return adapter(value)
        else:
            raise TypeError("adapter must be callable or provide write/save")


__all__ = [
    "ArtifactRef",
    "BatchResult",
    "BatchResultAdapter",
    "ColName",
    "CrashAdapter",
    "CrashSnapshotAdapter",
    "DatasetFinished",
    "DatasetResult",
    "DatasetStarted",
    "EventSink",
    "ExecutionEvent",
    "FunctionPipelineStep",
    "FunctionPipelineStepValidator",
    "PipelineContext",
    "PipelineCore",
    "PipelineError",
    "PipelineStepBase",
    "PipelineStepValidatorBase",
    "PipelineStepValidatorResult",
    "ResultCSVAdapter",
    "RunFinished",
    "RunStarted",
    "StepFailed",
    "StepResult",
    "StepSkipped",
    "StepStarted",
    "StepSucceeded",
    "append_step_result_csv",
    "write_crash_snapshot",
]
