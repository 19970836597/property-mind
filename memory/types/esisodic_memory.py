
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..base import BaseMemory, MemoryItem, MemoryConfig
from ..storage.doc_store import SQLiteDocumentStore
from ..embedding import get_text_embedder, get_dimension
from ..storage.qdrant import QdrantConnectionManager, QdrantVectorStore

logger = logging.getLogger(__name__)


class  Episode:
    """情景果子"""
    def __init__(
            self,
            episode_id:str,
            user_id:str,
            session_id:str,
            timestamp:float,
            context:Dict[str,Any],
            content:str,
            outcome:Optional[str]=None,
            ipt:float=0.5):
        self.episode_id=episode_id
        self.user_id=user_id
        self.session_id=session_id
        self.timestamp=timestamp
        self.content=content
        self.context=context
        self.outcome=outcome
        self.ipt=ipt
class EpisodeMemory(BaseMemory):
    """情景记忆
    
    1.存储具体的交互事件
    2.记录用户在情景中的行为和结果
    
    
    
    """
    def __init__(
            self,
            config:MemoryConfig,
            storage_backend=None
            ):
        super().__init__(config,storage_backend)

        #本地缓存
        self.episodes:List[Episode]=[]
        self.sessions:Dict[str,List[str]]={}



        #文档存储
        db_dir=self.config.storage_path if hasattr(self.config,"storage_path") else "./memory_data"
        os.makedirs(db_dir,exist_ok=True)
        db_path=os.path.join(db_dir,"memory.db")
        self.doc_store=SQLiteDocumentStore(db_path=db_path)



        #加载embedding模型
        self.embedder=get_text_embedder()

        #向量存储  （Qdrant）
        try:
            qd_url=os.getenv("QDRANT_URL")
            qd_api_key=os.getenv("QDRANT_API_KEY")
            self.vector_store=QdrantConnectionManager.get_instance(
                url=qd_url,
                api_key=qd_api_key,
                collection_name=os.getenv("QDRANT_COLLECTION_NAME","memory_vectors"),
                vector_size=get_dimension(default=getattr(self.embedder,"dimension",384)),
                distance=os.getenv("QDRANT_DISTANCE","cosine")
            )
        except Exception as e:
            logger.warning(f"Qdrant初始化失败，向量存储不可用: {e}")
            self.vector_store=None


    def add(
            self,
            memory_item:MemoryItem
    ):
        session_id=memory_item.metadata.get("session_id")
        context=memory_item.metadata.get("context",{})
        outcome=memory_item.metadata.get("outcome")
        
        partcipants = memory_item.metadata.get("participants",[])
        tags=memory_item.metadata.get("tags",[])


        episode=Episode(
            episode_id=memory_item.id,
            user_id=memory_item.user_id,
            session_id=session_id,
            timestamp=memory_item.timestamp,
            context=context,
            content=memory_item.content,
            outcome=outcome,
            ipt=memory_item.importance
        )
        self.episodes.append(episode)
        if session_id not in self.sessions:
            self.sessions[session_id]=[]
        self.sessions[session_id].append(episode.episode_id)



        #sqlite存储
        ts_int=int(memory_item.timestamp.timestamp())
        self.doc_store.add_memory(
            memory_id=memory_item.id,
            user_id=memory_item.user_id,
            content=memory_item.content,
            memory_type="episodic",
            timestamp=ts_int,
            importance=memory_item.importance,
            properties={
                "session_id":session_id,
                "context":context,
                "outcome":outcome,
                "partcipants":partcipants,
                "tags":tags 
            }
        )
        try:
            embedding=self.embedder.encode(memory_item.content)

            if hasattr(embedding,"tolist"):
                embedding=embedding.tolist()
            self.vector_store.add_vectors(
                    vectors=[embedding],
                    metadata=[{
                    "memory_id":memory_item.id,
                    "user_id":memory_item.user_id,
                    "memory_type":"episodic",
                    "importance":memory_item.importance,
                    "session_id":session_id,
                    "content":memory_item.content,
                }],
                ids=[memory_item.id] ) 
        except Exception as e:
            print("向量入库失败不影响权威存储")
            pass
        return memory_item.id

    def retrieve(
            self,
            query:str,
            limit:int=5,
            **kwargs
            ):
        user_id=kwargs.get("user_id")
        session_id=kwargs.get("session_id")
        time_range = kwargs.get("time_range")
        importance_threshold = kwargs.get("importance_threshold")
        limit = kwargs.get("limit", limit)

        #过滤
        candidate_ids = None
        if time_range or importance_threshold:
            start_time=int(time_range[0].timestamp()) if time_range else None 
            end_time=int(time_range[1].timestamp()) if time_range else None
            docs= self.doc_store.retrieve(
                user_id=user_id,
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                importance_threshold=importance_threshold   #limit 参数
            )
            #检索到的数据结构：List

            candidate_ids=set([doc.memory_id for doc in docs])
        try:
            query_vec=self.embedder.encode(query)
            if hasattr(query_vec,"tolist"):
                #有没有tolist方法  转成list 
                query_vec=query_vec.tolist()
            where = {"memory_type":"episodic"}
            if user_id:
                where["user_id"]=user_id
            hits = self.vector_store.search_similar(
                query_vector = query_vec,
                limit=limit,
                where=where
            )

        except Exception as e:
            hits=[]



        #过滤重排
        now_time=int(datetime.now().timestamp())

        results:List[Tuple[float,MemoryItem]]=[]
        seen=set()

        for hit in hits:
            meta=hit.get("metadata",{})
            mem_id=meta.get("memory_id")
            if not mem_id or mem_id in seen:
                continue
            #遗忘机制
            episode=next((e for e in self.episodes if e.episode_id==mem_id),None)
            if episode and episode.context.get("forget",False):
                continue
            if candidate_ids and mem_id not in candidate_ids: 
                continue
            #指定会话
            if session_id and meta.get("session_id")!=session_id:
                continue


            doc=self.doc_store.get_memory(mem_id)
            #判空
            if not doc:
                continue



            #score

            vec_score=hit.get("score",0.0) 
            age_days=max(0.0,(now_time-int(doc["timestamp"]))/86400.0)
            recency_score=1.0/(1.0+age_days)
            imp=float(doc.get("importance",0.5))

            #算法 
            base_relevance=vec_score*recency_score*imp
            importance_weight=0.8+(imp*0.4)

            combined=base_relevance*importance_weight
            item =MemoryItem(
                id=doc["memory_id"],
                user_id=doc["user_id"],
                content=doc["content"],
                memory_type=doc["memory_type"],
                timestamp=datetime.fromtimestamp(doc["timestamp"]),
                importance=doc["importance"],
                metadata={
                    **doc.get("properties",{}),
                    "relevance":combined,
                    "vector_score":vec_score,
                    "recency_score":recency_score,
                }
            )
            results.append((combined,item))
            seen.add(mem_id)
        if not results:
            # Qdrant is optional: after a process restart the in-memory episode
            # cache is empty, so use the durable SQLite store as the fallback.
            query_lower = (query or "").lower()
            docs = self.doc_store.search_memories(
                user_id=user_id,
                memory_type="episodic",
                limit=max(limit * 10, 50),
            )
            for doc in docs:
                content = str(doc.get("content", ""))
                if query_lower not in content.lower():
                    continue
                timestamp = datetime.fromtimestamp(int(doc["timestamp"]))
                importance = float(doc.get("importance", 0.5))
                recency_score = 1.0 / (1.0 + max(0.0, (now_time - int(doc["timestamp"])) / 86400.0))
                combined = 0.5 * recency_score * importance * (0.8 + importance * 0.4)
                item = MemoryItem(
                    id=doc["memory_id"],
                    user_id=doc["user_id"],
                    content=content,
                    memory_type="episodic",
                    timestamp=timestamp,
                    importance=importance,
                    metadata={
                        **(doc.get("properties") or {}),
                        "relevance": combined,
                        "vector_score": 0.0,
                        "recency_score": recency_score,
                    },
                )
                results.append((combined, item))
        results.sort(key=lambda x :x[0] ,reverse=True)
        return [item for score, item in results[:limit]]
    def update(

            self,
            memory_id:str,
            content:str,
            importance:float=None,
            metadata:Dict[str,Any]={}
    ):

        updated=False

        for episode in self.episodes:
            if episode.episode_id==memory_id:
                episode.content=content
                if importance is not None:
                    episode.importance=importance
                if metadata:
                    episode.context.update(metadata.get("context",{}))
                updated=True
                break
        #更新sqlite
        doc_updated=self.doc_store.update_memory(
            memory_id=memory_id,
            content=content,
            importance=importance,
            properties=metadata)   #context动态改变metadata

        if content:
            try:
                embedding=self.embedder.encode(content)
                if hasattr(embedding,"tolist"):
                    embedding=embedding.tolist()
                doc = self.doc_store.get_memory(memory_id)
                payload = {
                    "memory_id": memory_id,
                    "user_id": doc["user_id"] if doc else "",
                    "memory_type": "episodic",
                    "importance": (doc.get("importance") if doc else importance) or 0.5,
                    "session_id": (doc.get("properties", {}) or {}).get("session_id"),
                    "content": content
                }
                self.vector_store.add_vectors(
                    vectors=[embedding],
                    metadata=[payload],
                    ids=[memory_id]
                )
            except Exception:
                pass

        return updated or doc_updated

    def remove(self,memory_id:str)->bool:
        removed=False

        for i,episode in enumerate(self.episodes):
            if episode.episode_id==memory_id:
                removed_episode=self.episodes.pop(i)
                session_id=removed_episode.session_id
                if session_id in  self.sessions:
                    self.sessions[session_id].remove(memory_id)
                    if not self.sessions[session_id]:
                        del self.sessions[session_id]
                removed=True
                break
        doc_deleted=self.doc_store.delete_memory(memory_id)

        try:
            self.vector_store.delete_vectors([memory_id])
        except Exception:
            pass
        return removed and doc_deleted
    def has_memory(self, memory_id: str) -> bool:
        """检查记忆是否存在"""
        return any(episode.episode_id == memory_id for episode in self.episodes)
    
    def clear(self):
        """清空所有情景记忆（仅清理episodic，不影响其他类型）"""
        # 内存缓存
        self.episodes.clear()
        self.sessions.clear()
        self.patterns_cache.clear()

        # SQLite内的episodic全部删除
        docs = self.doc_store.search_memories(memory_type="episodic", limit=10000)
        ids = [d["memory_id"] for d in docs]
        for mid in ids:
            self.doc_store.delete_memory(mid)

        # Qdrant按ID删除对应向量
        try:
            if ids:
                self.vector_store.delete_memories(ids)
        except Exception:
            pass

    def forget(self,strategy:str,threshold:float=0.1,**kwargs)->bool:
        """
        忘记的方法：
        1.importance_based
        2.time_based
        3.capacity_based
        Args:
            strategy:str
            threshold:float=0.1
            **kwargs
                            """
        cnt=0
        current_time=datetime.now()
        forgeting_memory=[]
        logging.info(f"forgeting  memory based on \n方法：{strategy} 阈值{threshold}")

        for episode in self.episodes:
            forget=False
            if strategy=="importance_based":
                if episode.importance<threshold:
                    forget=True
                elif strategy=="time_based":
                    aware_line=current_time-timedelta(days=kwargs.get("aware_days",30))
                    if episode.timestamp<aware_line:
                        forget=True
                elif strategy=="capacity_based":
                    if len(self.episodes)>self.config.max_capacity:
                        sorted_episodes=sorted(self.episodes,key=lambda x:x.importance)
                        excess_count=len(sorted_episodes)-self.config.max_capacity
                        if episode in sorted_episodes[:excess_count]:
    
                            forget=True
            if forget:
                forgeting_memory.append(episode.episode_id)
        for episode_id in forgeting_memory:
            if self.remove(episode_id):
                cnt+=1
        logging.info(f"forget {cnt} memory")
        return cnt>0


    
    def get_all(self) -> List[MemoryItem]:
        """获取所有情景记忆（转换为MemoryItem格式）"""
        memory_items = []
        for episode in self.episodes:
            memory_item = MemoryItem(
                id=episode.episode_id,
                content=episode.content,
                memory_type="episodic",
                user_id=episode.user_id,
                timestamp=episode.timestamp,
                importance=episode.importance,
                metadata=episode.metadata
            )
            memory_items.append(memory_item)
        return memory_items
    
    def get_stats(self) -> Dict[str, Any]:
        """获取情景记忆统计信息（合并SQLite与Qdrant）"""
        # 硬删除模式：所有episodes都是活跃的
        active_episodes = self.episodes
        
        db_stats = self.doc_store.get_database_stats()
        try:
            vs_stats = self.vector_store.get_collection_stats()
        except Exception:
            vs_stats = {"store_type": "qdrant"}
        return {
            "count": len(active_episodes),  # 活跃记忆数量
            "forgotten_count": 0,  # 硬删除模式下已遗忘的记忆会被直接删除
            "total_count": len(self.episodes),  # 总记忆数量
            "sessions_count": len(self.sessions),
            "avg_importance": sum(e.importance for e in active_episodes) / len(active_episodes) if active_episodes else 0.0,
            "time_span_days": self._calculate_time_span(),
            "memory_type": "episodic",
            "vector_store": vs_stats,
            "document_store": {k: v for k, v in db_stats.items() if k.endswith("_count") or k in ["store_type", "db_path"]}
        }
    
    def get_session_episodes(self, session_id: str) -> List[Episode]:
        """获取指定会话的所有情景"""
        if session_id not in self.sessions:
            return []
        
        episode_ids = self.sessions[session_id]
        return [e for e in self.episodes if e.episode_id in episode_ids]
    
    def find_patterns(self, user_id: str = None, min_frequency: int = 2) -> List[Dict[str, Any]]:
        """发现用户行为模式"""
        # 检查缓存
        cache_key = f"{user_id}_{min_frequency}"
        if (cache_key in self.patterns_cache and 
            self.last_pattern_analysis and 
            (datetime.now() - self.last_pattern_analysis).hours < 1):
            return self.patterns_cache[cache_key]
        
        # 过滤情景
        episodes = [e for e in self.episodes if user_id is None or e.user_id == user_id]
        
        # 简单的模式识别：基于内容关键词
        keyword_patterns = {}
        context_patterns = {}
        
        for episode in episodes:
            # 提取关键词
            words = episode.content.lower().split()
            for word in words:
                if len(word) > 3:  # 忽略短词
                    keyword_patterns[word] = keyword_patterns.get(word, 0) + 1
            
            # 提取上下文模式
            for key, value in episode.context.items():
                pattern_key = f"{key}:{value}"
                context_patterns[pattern_key] = context_patterns.get(pattern_key, 0) + 1
        
        # 筛选频繁模式
        patterns = []
        
        for keyword, frequency in keyword_patterns.items():
            if frequency >= min_frequency:
                patterns.append({
                    "type": "keyword",
                    "pattern": keyword,
                    "frequency": frequency,
                    "confidence": frequency / len(episodes)
                })
        
        for context_pattern, frequency in context_patterns.items():
            if frequency >= min_frequency:
                patterns.append({
                    "type": "context",
                    "pattern": context_pattern,
                    "frequency": frequency,
                    "confidence": frequency / len(episodes)
                })
        
        # 按频率排序
        patterns.sort(key=lambda x: x["frequency"], reverse=True)
        
        # 缓存结果
        self.patterns_cache[cache_key] = patterns
        self.last_pattern_analysis = datetime.now()
        
        return patterns
    
    def get_timeline(self, user_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """获取时间线视图"""
        episodes = [e for e in self.episodes if user_id is None or e.user_id == user_id]
        episodes.sort(key=lambda x: x.timestamp, reverse=True)
        
        timeline = []
        for episode in episodes[:limit]:
            timeline.append({
                "episode_id": episode.episode_id,
                "timestamp": episode.timestamp.isoformat(),
                "content": episode.content[:100] + "..." if len(episode.content) > 100 else episode.content,
                "session_id": episode.session_id,
                "importance": episode.importance,
                "outcome": episode.outcome
            })
        
        return timeline
    
    def _filter_episodes(
        self,
        user_id: str = None,
        session_id: str = None,
        time_range: Tuple[datetime, datetime] = None
    ) -> List[Episode]:
        """过滤情景"""
        filtered = self.episodes
        
        if user_id:
            filtered = [e for e in filtered if e.user_id == user_id]
        
        if session_id:
            filtered = [e for e in filtered if e.session_id == session_id]
        
        if time_range:
            start_time, end_time = time_range
            filtered = [e for e in filtered if start_time <= e.timestamp <= end_time]
        
        return filtered
    
    def _calculate_time_span(self) -> float:
        """计算记忆时间跨度（天）"""
        if not self.episodes:
            return 0.0
        
        timestamps = [e.timestamp for e in self.episodes]
        min_time = min(timestamps)
        max_time = max(timestamps)
        
        return (max_time - min_time).days
    
    def _persist_episode(self, episode: Episode):
        """持久化情景到存储后端"""
        if self.storage and hasattr(self.storage, 'add_memory'):
            self.storage.add_memory(
                memory_id=episode.episode_id,
                user_id=episode.user_id,
                content=episode.content,
                memory_type="episodic",
                timestamp=int(episode.timestamp.timestamp()),
                importance=episode.importance,
                properties={
                    "session_id": episode.session_id,
                    "context": episode.context,
                    "outcome": episode.outcome
                }
            )
    
    def _remove_from_storage(self, memory_id: str):
        """从存储后端删除"""
        if self.storage and hasattr(self.storage, 'delete_memory'):
            self.storage.delete_memory(memory_id)
