from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ProviderResponse:
    text: str
    model_name: str
    model_version: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ModelProvider(ABC):
    """Provider abstraction for live or mock model calls."""

    @abstractmethod
    def complete(self, prompt: str) -> ProviderResponse:
        raise NotImplementedError
