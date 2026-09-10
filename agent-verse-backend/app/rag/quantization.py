"""Embedding quantization — int8 (scalar) and binary compression.

Full-precision float32 embeddings are accurate but heavy (a 2048-d vector is 8 KB).
Quantization trades a little accuracy for large memory/latency wins:

  * **int8** (scalar, symmetric per-vector): 4x smaller. Reconstruction is close;
    dot/cosine on the codes track the float values within a small tolerance. Good
    as the *stored* representation or a cheaper rescoring pass.
  * **binary** (sign bits, packed): 32x smaller. Similarity is estimated from
    Hamming distance. Coarser, but ideal as a *first-stage* filter that shortlists
    candidates for a full-precision rerank.

This is a dependency-free toolkit (pure Python + stdlib). The mode is config-driven
(``Settings.embedding_quantization``) so callers opt in; ``none`` keeps full
precision and is the default. Nothing here mutates stored data on its own — a
consumer chooses when to quantize.
"""

from __future__ import annotations

from dataclasses import dataclass

_INT8_MAX = 127


@dataclass(frozen=True)
class Int8Vector:
    """A symmetric int8-quantized embedding plus the scale to reconstruct it."""

    codes: tuple[int, ...]  # each in [-127, 127]
    scale: float  # x_approx = code * scale


def quantize_int8(vec: list[float]) -> Int8Vector:
    """Symmetric per-vector int8 quantization. scale = max|x| / 127."""
    if not vec:
        return Int8Vector(codes=(), scale=0.0)
    peak = max(abs(x) for x in vec)
    if peak == 0.0:
        return Int8Vector(codes=tuple(0 for _ in vec), scale=0.0)
    scale = peak / _INT8_MAX
    codes = tuple(_clamp_int8(round(x / scale)) for x in vec)
    return Int8Vector(codes=codes, scale=scale)


def dequantize_int8(q: Int8Vector) -> list[float]:
    return [code * q.scale for code in q.codes]


def int8_dot(a: Int8Vector, b: Int8Vector) -> float:
    """Dot product of two int8 vectors: scale_a * scale_b * Σ(a_i · b_i)."""
    if not a.codes or len(a.codes) != len(b.codes):
        return 0.0
    raw = sum(x * y for x, y in zip(a.codes, b.codes, strict=True))
    return a.scale * b.scale * raw


def int8_cosine(a: Int8Vector, b: Int8Vector) -> float:
    if not a.codes or len(a.codes) != len(b.codes):
        return 0.0
    dot = sum(x * y for x, y in zip(a.codes, b.codes, strict=True))
    na = sum(x * x for x in a.codes) ** 0.5
    nb = sum(x * x for x in b.codes) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _clamp_int8(value: int) -> int:
    return max(-_INT8_MAX, min(_INT8_MAX, value))


@dataclass(frozen=True)
class BinaryVector:
    """A sign-quantized embedding: one bit per dimension, packed into bytes."""

    bits: bytes
    dim: int  # original dimension (bits may be padded to a byte boundary)


def quantize_binary(vec: list[float]) -> BinaryVector:
    """Sign quantization: bit = 1 where x > 0, else 0. Packed MSB-first per byte."""
    dim = len(vec)
    out = bytearray((dim + 7) // 8)
    for i, x in enumerate(vec):
        if x > 0.0:
            out[i // 8] |= 1 << (7 - (i % 8))
    return BinaryVector(bits=bytes(out), dim=dim)


def hamming_distance(a: BinaryVector, b: BinaryVector) -> int:
    """Number of differing bits over the original dimensions."""
    if a.dim != b.dim:
        raise ValueError("binary vectors have different dimensions")
    diff = 0
    for byte_a, byte_b in zip(a.bits, b.bits, strict=True):
        diff += bin(byte_a ^ byte_b).count("1")
    return diff


def binary_cosine_estimate(a: BinaryVector, b: BinaryVector) -> float:
    """Estimate cosine from Hamming distance: 1 - 2·(hamming/dim), in [-1, 1].

    Under a sign/random-projection view, the fraction of agreeing sign bits
    approximates (1 + cos θ) / 2, so this recovers a cosine-like score.
    """
    if a.dim == 0:
        return 0.0
    agree_fraction = 1.0 - hamming_distance(a, b) / a.dim
    return 2.0 * agree_fraction - 1.0


class EmbeddingQuantizer:
    """Config-driven facade. ``mode`` is one of ``none``/``int8``/``binary``."""

    VALID_MODES = ("none", "int8", "binary")

    def __init__(self, mode: str = "none") -> None:
        mode = (mode or "none").lower()
        if mode not in self.VALID_MODES:
            raise ValueError(f"unknown quantization mode {mode!r}; use one of {self.VALID_MODES}")
        self.mode = mode

    @property
    def enabled(self) -> bool:
        return self.mode != "none"

    def compression_ratio(self) -> float:
        """Approximate storage reduction vs float32."""
        return {"none": 1.0, "int8": 4.0, "binary": 32.0}[self.mode]

    def encode(self, vec: list[float]) -> Int8Vector | BinaryVector | list[float]:
        if self.mode == "int8":
            return quantize_int8(vec)
        if self.mode == "binary":
            return quantize_binary(vec)
        return vec

    def similarity(self, a: object, b: object) -> float:
        """Cosine-like similarity in the active mode's representation."""
        if self.mode == "int8" and isinstance(a, Int8Vector) and isinstance(b, Int8Vector):
            return int8_cosine(a, b)
        if self.mode == "binary" and isinstance(a, BinaryVector) and isinstance(b, BinaryVector):
            return binary_cosine_estimate(a, b)
        if isinstance(a, list) and isinstance(b, list):
            return _float_cosine(a, b)
        raise TypeError("similarity operands do not match the quantizer mode")


def _float_cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank_by_binary(
    query: list[float], documents: list[list[float]]
) -> list[tuple[int, int]]:
    """First-stage shortlist: rank documents by Hamming distance to the query.

    Returns ``(original_index, hamming_distance)`` pairs, nearest first. Intended
    as a cheap prefilter before a full-precision rerank.
    """
    q = quantize_binary(query)
    scored = [(i, hamming_distance(q, quantize_binary(doc))) for i, doc in enumerate(documents)]
    scored.sort(key=lambda pair: pair[1])
    return scored
