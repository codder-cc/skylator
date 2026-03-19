"""Abstract base for all translation backends."""

from __future__ import annotations
import gc
from abc import ABC, abstractmethod
from enum import Enum, auto


class ModelState(Enum):
    UNLOADED = auto()
    LOADED   = auto()


class BaseBackend(ABC):
    """
    All backends implement load/unload/translate.
    Sequential usage pattern:
        backend.load()
        results = backend.translate(texts, context)
        backend.unload()   # frees VRAM before next model loads
    """

    def __init__(self):
        self._state = ModelState.UNLOADED

    @property
    def is_loaded(self) -> bool:
        return self._state == ModelState.LOADED

    @abstractmethod
    def load(self) -> None:
        """Load model weights into VRAM."""

    @abstractmethod
    def translate(self, texts: list[str], context: str = "") -> list[str]:
        """
        Translate a list of strings.
        Returns a list of the same length.
        Never raises — returns original texts on error.
        """

    def unload(self) -> None:
        """Free GPU memory. Call between model swaps."""
        self._do_unload()
        self._state = ModelState.UNLOADED
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
        gc.collect()

    def _do_unload(self) -> None:
        """Override in subclasses to release model references."""

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, *_):
        self.unload()
