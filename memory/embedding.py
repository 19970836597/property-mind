import os
import re
import threading
from typing import List, Optional, Union

import numpy as np
import hashlib


class EmbeddingModel:
    """嵌入模型基类"""

    def encode(self,text:Union[str,List[str]]):
        raise NotImplementedError  #基类必须实现



    @property
    def dimension(self)->int:
        raise NotImplementedError  #基类必须实现  一个属性 
    #对于描述对象固有特征的属性（维度、大小、颜色等），用 @property 更自然。



class LocalTransformerEmbedding(EmbeddingModel):



    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._backend = None  # "st" 或 "hf"
        self._st_model = None
        self._hf_tokenizer = None
        self._hf_model = None
        self._dimension = None
        self._load_backend()
    #加载配置
    def _load_backend(self):
         # 优先 sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            try:
                # Prefer the local cache to avoid slow network probes on every start.
                self._st_model = SentenceTransformer(
                    self.model_name,
                    local_files_only=True,
                )
            except Exception:
                self._st_model = SentenceTransformer(self.model_name)
            test_vec = self._st_model.encode("test_text")
            self._dimension = len(test_vec)
            #检测是否能够运行
            self._backend = "st"
            return
        except Exception:
            self._st_model = None

            # 回退 transformers
            try:
                from transformers import AutoTokenizer, AutoModel
                import torch
                self._hf_tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                self._hf_model = AutoModel.from_pretrained(self.model_name)
                with torch.no_grad():
                    inputs = self._hf_tokenizer("test_text", return_tensors="pt", padding=True, truncation=True)
                    outputs = self._hf_model(**inputs)
                    test_embedding = outputs.last_hidden_state.mean(dim=1)
                    self._dimension = int(test_embedding.shape[1])
                self._backend = "hf"
                return
            except Exception:
                self._hf_tokenizer = None
                self._hf_model = None

            raise ImportError("未找到可用的本地嵌入后端，请安装 sentence-transformers 或 transformers+torch")

    def encode(self, texts: Union[str, List[str]]):
        if isinstance(texts, str):
            inputs = [texts]
            single = True
        else:
            inputs = list(texts)
            single = False

        if self._backend == "st":
            vecs = self._st_model.encode(inputs)
            if hasattr(vecs, "tolist"):
                vecs = [v for v in vecs]
        else:
            import torch
            tokenized = self._hf_tokenizer(inputs, return_tensors="pt", padding=True, truncation=True, max_length=512)
            with torch.no_grad():
                outputs = self._hf_model(**tokenized)
                embeddings = outputs.last_hidden_state.mean(dim=1).cpu().numpy()
            vecs = [v for v in embeddings]

        if single:
            return vecs[0]
        return vecs

    @property
    def dimension(self) -> int:
        return int(self._dimension or 0)



class TFIDFEmbedding(EmbeddingModel):
    """TF-IDF 简易兜底（在无深度模型时保证可用）"""

    def __init__(self, max_features: int = 1000):
        self.max_features = max_features
        self._vectorizer = None
        self._is_fitted = False
        self._dimension = max_features
        self._init_vectorizer()

    def _init_vectorizer(self):
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._vectorizer = TfidfVectorizer(max_features=self.max_features, stop_words='english')
        except ImportError:
            raise ImportError("请安装 scikit-learn: pip install scikit-learn")

    def fit(self, texts: List[str]):
        self._vectorizer.fit(texts)
        self._is_fitted = True
        self._dimension = len(self._vectorizer.get_feature_names_out())

    def encode(self, texts: Union[str, List[str]]):
        if not self._is_fitted:
            raise ValueError("TF-IDF模型未训练，请先调用fit()方法")
        if isinstance(texts, str):
            texts = [texts]
            single = True
        else:
            single = False
        tfidf_matrix = self._vectorizer.transform(texts)
        embeddings = tfidf_matrix.toarray()
        if single:
            return embeddings[0]
        return [e for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dimension


class HashEmbedding(EmbeddingModel):
    """Deterministic fallback that works without network or ML dependencies."""

    def __init__(self, dimension: int = 384):
        self._dimension = dimension

    def encode(self, texts: Union[str, List[str]]):
        single = isinstance(texts, str)
        inputs = [texts] if single else list(texts)
        vectors = []
        for text in inputs:
            vector = np.zeros(self._dimension, dtype=np.float32)
            for index in range(max(1, len(text))):
                digest = hashlib.blake2b(
                    f"{text}:{index}".encode("utf-8"), digest_size=8
                ).digest()
                bucket = int.from_bytes(digest[:4], "little") % self._dimension
                vector[bucket] += 1.0 if digest[4] & 1 else -1.0
            norm = np.linalg.norm(vector)
            vectors.append(vector / norm if norm else vector)
        return vectors[0] if single else vectors

    @property
    def dimension(self) -> int:
        return self._dimension


class DashScopeEmbedding(EmbeddingModel):
    """阿里云 DashScope（通义千问）Embedding / OpenAI兼容REST 模式

    行为：
    - 如提供 base_url，则优先使用 OpenAI 兼容的 REST 接口（POST {base_url}/embeddings）。
    - 否则使用官方 dashscope SDK 的 TextEmbedding.call。
    """

    def __init__(self, model_name: str = "text-embedding-v3", api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self._dimension = None
        # 仅在非REST情况下初始化SDK
        if not self.base_url:
            self._init_client()
        # 探测维度
        test = self.encode("health_check")
        self._dimension = len(test)

    def _init_client(self):
        try:
            if self.api_key:
                # 将统一命名的 API Key 注入到 SDK 期望的位置
                os.environ["DASHSCOPE_API_KEY"] = self.api_key
            import dashscope  # noqa: F401
        except ImportError:
            raise ImportError("请安装 dashscope: pip install dashscope")

    def encode(self, texts: Union[str, List[str]]):
        if isinstance(texts, str):
            inputs = [texts]
            single = True
        else:
            inputs = list(texts)
            single = False

        # REST 模式（OpenAI兼容）
        if self.base_url:
            import requests
            url = self.base_url.rstrip("/") + "/embeddings"
            headers = {
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                "Content-Type": "application/json",
            }
            payload = {"model": self.model_name, "input": inputs}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code >= 400:
                raise RuntimeError(f"Embedding REST 调用失败: {resp.status_code} {resp.text}")
            data = resp.json()
            # 期望结构：{"data": [{"embedding": [...]}]}
            items = data.get("data") or []
            vecs = [np.array(item.get("embedding")) for item in items]
            if single:
                return vecs[0]
            return vecs

        # SDK 模式
        from dashscope import TextEmbedding
        rsp = TextEmbedding.call(model=self.model_name, input=inputs)
        embeddings_obj = None
        if isinstance(rsp, dict):
            embeddings_obj = (rsp.get("output") or {}).get("embeddings")
        else:
            embeddings_obj = getattr(getattr(rsp, "output", None), "embeddings", None)
        if not embeddings_obj:
            raise RuntimeError("DashScope 返回为空或格式不匹配")
        vecs = [np.array(item.get("embedding") or item.get("vector")) for item in embeddings_obj]
        if single:
            return vecs[0]
        return vecs

    @property
    def dimension(self) -> int:
        return int(self._dimension or 0)

def create_embedding_model(model_type: str = "local", **kwargs) -> EmbeddingModel:
    """创建嵌入模型实例

    model_type: "dashscope" | "local" | "tfidf"
    kwargs: model_name, api_key
    """
    if model_type in ("local", "sentence_transformer", "huggingface"):
        return LocalTransformerEmbedding(**kwargs)
    elif model_type == "dashscope":
        return DashScopeEmbedding(**kwargs)
    elif model_type == "tfidf":
        return TFIDFEmbedding(**kwargs)
    else:
        raise ValueError(f"不支持的模型类型: {model_type}")


def create_embedding_model_with_fallback(preferred_type: str = "dashscope", **kwargs) -> EmbeddingModel:
    """带回退的自动创建，不需一个个适用，工业级：dashscope -> local -> tfidf"""
    if preferred_type in ("sentence_transformer", "huggingface"):
        preferred_type = "local"
    fallback = ["dashscope", "local", "tfidf"]
    # 将首选放最前
    if preferred_type in fallback:
        fallback.remove(preferred_type)
        fallback.insert(0, preferred_type)
    for t in fallback:
        try:
            return create_embedding_model(t, **kwargs)
        except Exception:
            continue
    # Allow startup and offline tests even when optional providers are absent.
    return HashEmbedding()

# ==================
# Provider（单例）
# ==================

_lock = threading.RLock()
_embedder: Optional[EmbeddingModel] = None


def _build_embedder() -> EmbeddingModel:
    preferred = os.getenv("EMBED_MODEL_TYPE", "dashscope").strip()
    default_model = "text-embedding-v3" if preferred == "dashscope" else "sentence-transformers/all-MiniLM-L6-v2"
    model_name = os.getenv("EMBED_MODEL_NAME", default_model).strip()
    kwargs = {}
    if model_name:
        kwargs["model_name"] = model_name
    api_key = os.getenv("EMBED_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if preferred == "dashscope" and api_key:
        kwargs["api_key"] = api_key
    base_url = os.getenv("EMBED_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL")
    if preferred == "dashscope" and base_url:
        kwargs["base_url"] = base_url
    return create_embedding_model_with_fallback(preferred_type=preferred, **kwargs)


def get_text_embedder() -> EmbeddingModel:
    """获取全局共享的文本嵌入实例（线程安全单例）"""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is None:
            _embedder = _build_embedder()
        return _embedder


def get_dimension(default: int = 384) -> int:
    """获取统一向量维度（失败回退默认值）"""
    try:
        return int(getattr(get_text_embedder(), "dimension", default))
    except Exception:
        return int(default)


def embedding_collection_name(
    prefix: str = "memory_vectors",
    explicit_env: Optional[str] = None,
) -> str:
    """Build a versioned collection name for the active embedding model."""
    explicit = os.getenv(explicit_env or "", "").strip()
    if explicit:
        return explicit
    model_name = os.getenv("EMBED_MODEL_NAME", "").strip()
    if not model_name:
        model_name = type(get_text_embedder()).__name__
    slug = re.sub(r"[^a-z0-9]+", "_", model_name.lower()).strip("_")
    slug = slug.removeprefix("sentence_transformers_")[-48:]
    return f"{prefix}_{slug}_{get_dimension(384)}_v1"


def refresh_embedder() -> EmbeddingModel:
    """强制重建嵌入实例（可用于动态切换环境变量）"""
    global _embedder
    with _lock:
        _embedder = _build_embedder()
        return _embedder
