"""int8 (scalar) and binary embedding quantization."""
from __future__ import annotations

import math
import random

import pytest

from app.rag.quantization import (
    BinaryVector,
    EmbeddingQuantizer,
    Int8Vector,
    binary_cosine_estimate,
    dequantize_int8,
    hamming_distance,
    int8_cosine,
    quantize_binary,
    quantize_int8,
    rank_by_binary,
)


def _float_cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ── int8 ──────────────────────────────────────────────────────────────────────


def test_int8_roundtrip_error_is_small() -> None:
    vec = [0.5, -0.25, 0.9, -0.9, 0.1, 0.0]
    q = quantize_int8(vec)
    approx = dequantize_int8(q)
    # Max abs reconstruction error < one quantization step (peak/127).
    step = max(abs(x) for x in vec) / 127
    assert all(abs(a - b) <= step for a, b in zip(vec, approx, strict=True))


def test_int8_cosine_tracks_float_cosine() -> None:
    rng = random.Random(42)
    for _ in range(20):
        a = [rng.uniform(-1, 1) for _ in range(64)]
        b = [rng.uniform(-1, 1) for _ in range(64)]
        fcos = _float_cosine(a, b)
        qcos = int8_cosine(quantize_int8(a), quantize_int8(b))
        assert abs(fcos - qcos) < 0.02  # within 2% of full precision


def test_int8_zero_and_empty() -> None:
    assert quantize_int8([]) == Int8Vector(codes=(), scale=0.0)
    z = quantize_int8([0.0, 0.0])
    assert z.codes == (0, 0)
    assert int8_cosine(z, z) == 0.0


def test_int8_codes_stay_in_range() -> None:
    q = quantize_int8([1000.0, -1000.0, 3.0])
    assert all(-127 <= c <= 127 for c in q.codes)
    assert max(q.codes) == 127 and min(q.codes) == -127


# ── binary ────────────────────────────────────────────────────────────────────


def test_binary_identical_vectors_zero_hamming() -> None:
    v = [0.3, -0.1, 0.8, -0.5, 0.2]
    assert hamming_distance(quantize_binary(v), quantize_binary(v)) == 0


def test_binary_opposite_signs_max_hamming() -> None:
    a = quantize_binary([1.0, 1.0, 1.0, 1.0])
    b = quantize_binary([-1.0, -1.0, -1.0, -1.0])
    assert hamming_distance(a, b) == 4
    assert binary_cosine_estimate(a, b) == pytest.approx(-1.0)
    assert binary_cosine_estimate(a, a) == pytest.approx(1.0)


def test_binary_dim_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="different dimensions"):
        hamming_distance(BinaryVector(b"\x00", 8), BinaryVector(b"\x00\x00", 16))


def test_binary_preserves_ordering_for_separable_vectors() -> None:
    query = [1.0, 1.0, 1.0, 1.0, 0.0, 0.0]
    near = [0.9, 0.8, 1.0, 0.7, 0.1, -0.1]  # same sign pattern → close
    far = [-1.0, -0.5, -0.9, -0.8, 0.9, 0.9]  # opposite → far
    q = quantize_binary(query)
    assert hamming_distance(q, quantize_binary(near)) < hamming_distance(
        q, quantize_binary(far)
    )


def test_rank_by_binary_shortlists_nearest_first() -> None:
    query = [1.0, 1.0, 1.0, 1.0]
    docs = [
        [-1.0, -1.0, -1.0, -1.0],  # idx 0: opposite → farthest
        [1.0, 1.0, 1.0, -1.0],  # idx 1: one bit off
        [1.0, 1.0, 1.0, 1.0],  # idx 2: identical → nearest
    ]
    ranked = rank_by_binary(query, docs)
    assert ranked[0][0] == 2  # identical first
    assert ranked[0][1] == 0  # zero hamming
    assert ranked[-1][0] == 0  # opposite last


# ── EmbeddingQuantizer facade ─────────────────────────────────────────────────


def test_quantizer_modes_and_ratios() -> None:
    assert EmbeddingQuantizer("none").compression_ratio() == 1.0
    assert EmbeddingQuantizer("int8").compression_ratio() == 4.0
    assert EmbeddingQuantizer("binary").compression_ratio() == 32.0
    assert EmbeddingQuantizer("none").enabled is False
    assert EmbeddingQuantizer("int8").enabled is True


def test_quantizer_invalid_mode() -> None:
    with pytest.raises(ValueError, match="unknown quantization mode"):
        EmbeddingQuantizer("float4")


def test_quantizer_encode_and_similarity_dispatch() -> None:
    a = [0.5, -0.5, 0.9, 0.1]
    b = [0.4, -0.6, 0.8, 0.2]

    none_q = EmbeddingQuantizer("none")
    assert none_q.encode(a) == a
    assert math.isclose(none_q.similarity(a, b), _float_cosine(a, b))

    i8 = EmbeddingQuantizer("int8")
    ea, eb = i8.encode(a), i8.encode(b)
    assert isinstance(ea, Int8Vector)
    assert abs(i8.similarity(ea, eb) - _float_cosine(a, b)) < 0.02

    bq = EmbeddingQuantizer("binary")
    ba, bb = bq.encode(a), bq.encode(b)
    assert isinstance(ba, BinaryVector)
    assert -1.0 <= bq.similarity(ba, bb) <= 1.0
