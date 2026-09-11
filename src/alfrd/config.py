"""Generic consumer configuration primitives compatible with AVICA."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

# Consumers can map Python-facing attribute names to external configuration
# names.  It intentionally starts empty, matching AVICA's established shape.
CONFIG_MAPPING: dict[str, str] = {}


class BaseConfig(type):
    """Metaclass that gives configuration classes a mapped ``data`` view."""

    def __new__(
        metaclass: type[BaseConfig],
        name: str,
        bases: tuple[type[Any], ...],
        namespace: dict[str, Any],
    ) -> BaseConfig:
        def get_data(instance: Config) -> dict[str, Any]:
            data: dict[str, Any] = {}
            combined = {**type(instance).__dict__, **instance.__dict__}
            for key, value in combined.items():
                if key.startswith("_") or key == "data":
                    continue
                if isinstance(value, (classmethod, staticmethod, property)) or callable(value):
                    continue
                data[CONFIG_MAPPING.get(key, key)] = value
            return data

        namespace["data"] = property(get_data)
        return super().__new__(metaclass, name, bases, namespace)


class Config(metaclass=BaseConfig):
    """Base class for lightweight attribute-based consumer configurations."""

    data: dict[str, Any]
    _source: Path | None

    def __init__(self, **values: Any) -> None:
        for key, value in values.items():
            setattr(self, key, value)

    @property
    def source(self) -> Path | None:
        return getattr(self, "_source", None)

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Any], *, source: str | Path | None = None
    ) -> Config:
        if not isinstance(values, Mapping):
            raise TypeError("Configuration values must be a mapping")
        config = cls(**dict(values))
        if source is not None:
            config._source = Path(source).expanduser().resolve()
        return config

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        config_path = Path(path).expanduser().resolve()
        try:
            values = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Could not read YAML config {config_path}: {exc}") from exc
        if not isinstance(values, Mapping):
            raise ValueError("YAML config root must be a mapping/object")
        return cls.from_mapping(values, source=config_path)

    def to_dict(self) -> dict[str, Any]:
        return self.data


__all__ = ["BaseConfig", "CONFIG_MAPPING", "Config"]
