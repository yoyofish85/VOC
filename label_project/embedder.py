# -*- coding: utf-8 -*-
"""S8：文本嵌入接口。

- OllamaEmbedder：调用 /api/embeddings（推荐 bge-m3）
- HashingEmbedder：离线确定性 n-gram 哈希（无模型可重放）
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import List, Optional, Protocol, Sequence

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


def _l2_normalize(vec: List[float]) -> List[float]:
    s = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / s for x in vec]


class HashingEmbedder:
    """字符 n-gram 哈希向量，纯 CPU、确定性、无外网。"""

    def __init__(self, dim: int = 256, ngram: int = 3):
        self.name = f"hashing_ngram{ngram}_d{dim}"
        self.dim = int(dim)
        self.ngram = int(ngram)

    def _one(self, text: str) -> List[float]:
        t = re.sub(r"\s+", "", (text or "").lower())
        vec = [0.0] * self.dim
        if len(t) < self.ngram:
            t = (t + "·" * self.ngram)[: self.ngram]
        for i in range(max(len(t) - self.ngram + 1, 1)):
            gram = t[i : i + self.ngram]
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._one(t) for t in texts]


class OllamaEmbedder:
    """Ollama embeddings API。默认模型名可用 VOC_EMBED_MODEL 覆盖。"""

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.model = (
            model
            or os.environ.get("VOC_EMBED_MODEL", "").strip()
            or "bge-m3"
        )
        self.host = (
            host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.name = f"ollama:{self.model}"
        self.timeout = timeout

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if httpx is None:
            raise RuntimeError("httpx 不可用，无法调用 Ollama embeddings")
        out: List[List[float]] = []
        with httpx.Client(timeout=self.timeout) as client:
            for text in texts:
                r = client.post(
                    f"{self.host}/api/embeddings",
                    json={"model": self.model, "prompt": text or " "},
                )
                r.raise_for_status()
                data = r.json()
                vec = data.get("embedding") or data.get("embeddings")
                if isinstance(vec, list) and vec and isinstance(vec[0], list):
                    vec = vec[0]
                if not isinstance(vec, list) or not vec:
                    raise RuntimeError(f"Ollama embeddings 空响应 model={self.model}")
                out.append(_l2_normalize([float(x) for x in vec]))
        return out

    def available(self) -> bool:
        if httpx is None:
            return False
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                names = [m.get("name", "") for m in (r.json().get("models") or [])]
                return any(
                    self.model == n or n.startswith(self.model + ":") for n in names
                )
        except Exception:
            return False


def get_embedder(prefer_ollama: bool = True) -> Embedder:
    """优先 Ollama bge-m3；不可用则 hashing 降级。"""
    if prefer_ollama:
        oe = OllamaEmbedder()
        if oe.available():
            return oe
    return HashingEmbedder()
