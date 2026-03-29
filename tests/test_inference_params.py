"""
Tests for translator/models/inference_params.py — InferenceParams

Covers:
- defaults() factory: all fields are None except thinking=False
- as_dict(): serializes all fields including None values
- from_dict(): deserializes correctly, missing keys → None
- Round-trip: as_dict() → from_dict() identity
- batch_size, temperature, max_tokens set correctly
"""
import pytest
from translator.models.inference_params import InferenceParams


class TestInferenceParams:
    def test_defaults_all_none(self):
        p = InferenceParams.defaults()
        assert p.temperature     is None
        assert p.top_p           is None
        assert p.top_k           is None
        assert p.max_tokens      is None
        assert p.batch_size      is None
        assert p.system_prompt   is None
        assert p.thinking        is False

    def test_as_dict_has_all_keys(self):
        p = InferenceParams(temperature=0.3, max_tokens=512, batch_size=4)
        d = p.as_dict()
        assert "temperature"  in d
        assert "max_tokens"   in d
        assert "batch_size"   in d
        assert d["temperature"] == 0.3
        assert d["max_tokens"]  == 512

    def test_from_dict_basic(self):
        p = InferenceParams.from_dict({"temperature": 0.5, "batch_size": 8})
        assert p.temperature == 0.5
        assert p.batch_size  == 8

    def test_from_dict_missing_keys_are_none(self):
        p = InferenceParams.from_dict({})
        assert p.temperature is None
        assert p.batch_size  is None

    def test_round_trip(self):
        original = InferenceParams(
            temperature=0.7, top_p=0.9, top_k=40,
            max_tokens=1024, batch_size=4,
            system_prompt="Translate well", thinking=True,
        )
        reconstructed = InferenceParams.from_dict(original.as_dict())
        assert reconstructed.temperature   == original.temperature
        assert reconstructed.top_p         == original.top_p
        assert reconstructed.max_tokens    == original.max_tokens
        assert reconstructed.batch_size    == original.batch_size
        assert reconstructed.system_prompt == original.system_prompt
        assert reconstructed.thinking      == original.thinking

    def test_thinking_default_false(self):
        p = InferenceParams()
        assert p.thinking is False

    def test_thinking_serialized(self):
        p = InferenceParams(thinking=True)
        d = p.as_dict()
        assert d.get("thinking") is True
