from __future__ import annotations

from abc import ABC, abstractmethod


class OtiBackend(ABC):
    """Interface used by the transformer instead of hard-coded OTIS names."""

    direction_count: int

    @abstractmethod
    def scalar_type(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def module_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def module_source(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def seed_call(self, variable: str, direction: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def real_part(self, expression: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def derivative_part(self, expression: str, direction: str) -> str:
        raise NotImplementedError
