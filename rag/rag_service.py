"""
总结服务类：将用户提问和参考资料给模型进行总结回复
"""
import re
import threading
import concurrent.futures
from typing import TYPE_CHECKING

from rag.vector_store import VectorStoreService
from utils.config_handler import chroma_conf
from utils.prompt_loader import load_rag_prompts
from utils.logger_handler import logger
from langchain_core.prompts import PromptTemplate
from model.factory import get_chat_model
from langchain_core.output_parsers import StrOutputParser

# 使用 TYPE_CHECKING 避免循环导入
if TYPE_CHECKING:
    from services.coze_service import CozeService

class RagSummarizeService(object):
    """RAG 服务入口，负责检索、重排、总结和来源整理。"""

    def __init__(self):
        """初始化向量库、提示词链路和检索相关参数。"""
        self.vector_store = VectorStoreService()
        self._collection_ready_checked = False
        self._repair_lock = threading.Lock()
        self._loading_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._load_future = None
        self.prompt_text = load_rag_prompts()
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)
        self.model = get_chat_model()
        self.chain = self._init_chain()
        self.top_k = chroma_conf["k"]
        self.candidate_k = chroma_conf.get("candidate_k", max(self.top_k * 2, self.top_k))
        self.min_relevance_score = chroma_conf.get("min_relevance_score", 0.0)
        self.synonym_map = {
            "不回充": ["回充失败", "无法返回充电座", "找不到充电座", "回不去充电座"],
            "回不了充": ["回充失败", "无法返回充电座", "回不去充电座"],
            "回不去": ["回充失败", "无法返回"],
            "迷路": ["定位异常", "建图异常", "导航异常", "找不到位置"],
            "漏扫": ["清扫遗漏", "覆盖率低", "扫不干净", "打扫不全"],
            "水痕": ["拖地水痕", "拖布湿度", "地面残留水渍", "水印", "水渍"],
            "缠头发": ["毛发缠绕", "边刷缠绕", "主刷缠绕", "卷头发"],
            "噪音大": ["异响", "噪声异常", "声音大", "很吵"],
            "不出水": ["拖地不出水", "水箱异常", "没水", "不喷水"],
            "卡住": ["脱困失败", "避障失败", "卡住了", "动不了"],
            "不工作": ["不启动", "没反应", "不运行"],
            "电池": ["续航", "电量", "充电", "电池寿命"],
            "清洁": ["打扫", "清扫", "拖地", "清洗"],
        }
        self.stopwords = {
            "的", "了", "呢", "吗", "呀", "啊", "一下",
            "是否", "一个", "可以", "需要", "有没有", "如何",
        }
        self._coze_service = None  # 懒加载 Coze 服务

    @property
    def coze_service(self) -> "CozeService":
        """懒加载 Coze 服务"""
        if self._coze_service is None:
            from services.coze_service import CozeService
            self._coze_service = CozeService()
        return self._coze_service

    def _init_chain(self):
        """构造“提示词 -> 模型 -> 文本解析”的最小总结链。"""
        chain = self.prompt_template | self.model | StrOutputParser()
        return chain

    def _ensure_collection_ready(self):
        """
        向量库为空时自动触发一次本地知识入库，避免首次使用直接空检索。
        使用后台线程异步加载，避免阻塞启动。
        """
        if self._collection_ready_checked:
            return
        self._collection_ready_checked = True

        try:
            current_count = self.vector_store.vector_store._collection.count()
        except Exception as e:
            logger.error(f"获取向量库文档数量失败: {str(e)}", exc_info=True)
            return

        if current_count > 0:
            logger.info(f"当前向量库已有文档，数量: {current_count}")
            return

        logger.warning("检测到向量库为空，将在后台异步加载知识文档")
        # 后台异步加载，避免阻塞启动
        self._load_future = self._loading_executor.submit(self._async_load_documents)

    def _async_load_documents(self):
        """后台异步加载知识文档"""
        try:
            self.vector_store.load_document()
            latest_count = self.vector_store.vector_store._collection.count()
            logger.info(f"后台自动加载完成，当前向量库文档数量: {latest_count}")
        except Exception as e:
            logger.error(f"后台自动加载知识文档失败: {str(e)}", exc_info=True)

    @staticmethod
    def _is_corrupted_index_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "hnsw segment reader" in message
            or "nothing found on disk" in message
            or "error executing plan" in message
        )

    def _repair_vector_store(self):
        """在检测到索引损坏时，串行重建向量库，避免并发修复。"""
        with self._repair_lock:
            logger.warning("检测到向量索引异常，开始重建向量库")
            self.vector_store.reset_store(clear_md5=True)
            self.vector_store.load_document(force_reload=True)
            self._collection_ready_checked = True
            latest_count = self.vector_store.get_collection_count()
            logger.info(f"向量库重建完成，当前文档数量: {latest_count}")

    @staticmethod
    def _normalize_query(query: str) -> str:
        """对用户问题做轻量规范化，统一一些常见别名。"""
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        replacements = {
            "扫拖一体": "扫拖一体机器人",
            "回充座": "充电座",
            "基站": "充电座",
            "回基站": "回充",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _expand_query(self, query: str, chat_history: str = "") -> str:
        """把口语化问题扩展成更适合检索的表达，可结合历史上下文。"""
        normalized = self._normalize_query(query)

        # 如果查询中有指代词，尝试从历史中恢复上下文
        refer_words = ["它", "这个", "那个", "这款", "这台", "我的", "刚才"]
        has_reference = any(word in normalized for word in refer_words)

        if has_reference and chat_history:
            # 从历史中提取产品相关关键词
            product_keywords = []
            for keyword in ["扫地机器人", "扫拖一体", "吸尘器", "回充", "拖地", "扫地"]:
                if keyword in chat_history.lower():
                    product_keywords.append(keyword)
            if product_keywords:
                normalized = f"{' '.join(product_keywords)} {normalized}"

        expansions = []
        for phrase, candidates in self.synonym_map.items():
            if phrase in normalized:
                expansions.extend(candidates)
        if expansions:
            normalized = f"{normalized} {' '.join(expansions)}"
        return normalized

    def _query_terms(self, query: str) -> set[str]:
        """提取检索关键词，供后续重排计算覆盖率。"""
        expanded = self._expand_query(query)
        terms = set()
        for term in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]+", expanded):
            if term not in self.stopwords:
                terms.add(term)
        return terms

    def _fallback_to_external_api(self, query: str, chat_history: str, reason: str):
        """
        当知识库内容不足时，尝试调用 Coze API 获取答案，并自动保存到知识库。

        Args:
            query: 用户原始问题
            chat_history: 对话历史
            reason: 触发 fallback 的原因

        Returns:
            Coze 返回的答案（已保存到知识库）
        """
        logger.warning(f"知识库内容不足，触发 Coze fallback: {reason}")

        # 调用 Coze 服务并自动保存回答到知识库
        success, answer = self.coze_service.chat_and_save(query, chat_history)

        if success and answer:
            logger.info(f"Coze 成功回答问题并已保存到知识库")
            return answer
        else:
            # Coze 也失败时的降级方案
            logger.error("Coze fallback 也失败，返回默认提示")
            return (
                f"抱歉，知识库中暂无关于\"{query[:30]}...\"的详细解答。\n"
                "这可能是因为：\n"
                "1. 这是一个较为罕见的故障情况\n"
                "2. 涉及硬件拆机或专业维修判断\n"
                "\n"
                "建议您：\n"
                "- 联系官方客服获得专业支持\n"
                "- 提供更多信息以便我们完善知识库"
            )

    @staticmethod
    def _document_terms(content: str) -> set[str]:
        """把文档内容切成词项集合，便于和 query 做简单交集比较。"""
        return set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]+", content.lower()))

    def _rerank_score(self, query_terms: set[str], content: str, relevance_score: float) -> float:
        """
        组合向量分数和关键词覆盖率。

        这里不是完整 reranker，而是一个成本很低的启发式重排，
        用来避免“向量相似但关键词没对上”的片段排得过高。
        """
        doc_terms = self._document_terms(content)
        overlap = len(query_terms & doc_terms)
        coverage = overlap / max(len(query_terms), 1)
        return relevance_score * 0.7 + coverage * 0.3

    def _wait_for_loading(self, timeout: float = 30.0):
        """等待后台加载完成，最多等待 timeout 秒"""
        if self._load_future and not self._load_future.done():
            try:
                self._load_future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("等待知识文档加载超时，将使用当前可用文档进行检索")
            except Exception as e:
                logger.error(f"等待知识文档加载失败: {str(e)}")

    def retriever_docs(self, query: str, chat_history: str = ""):
        """执行检索主流程：查询扩展 -> 候选召回 -> 轻量重排 -> 截断返回。"""
        self._ensure_collection_ready()
        # 等待后台加载完成（最多30秒）
        self._wait_for_loading(timeout=30.0)
        expanded_query = self._expand_query(query, chat_history)
        query_terms = self._query_terms(query)
        try:
            candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                expanded_query,
                k=self.candidate_k,
            )
        except Exception as e:
            logger.error(f"向量检索失败: {str(e)}", exc_info=True)
            if self._is_corrupted_index_error(e):
                try:
                    self._repair_vector_store()
                    candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                        expanded_query,
                        k=self.candidate_k,
                    )
                except Exception as repair_error:
                    logger.error(f"重建后检索仍失败: {str(repair_error)}", exc_info=True)
                    return []
            else:
                return []

        # 先保留候选，再按自定义分数重新排序。
        scored_docs = []
        for doc, relevance_score in candidates:
            if relevance_score < self.min_relevance_score:
                continue
            rerank_score = self._rerank_score(query_terms, doc.page_content, relevance_score)
            doc.metadata["relevance_score"] = round(float(relevance_score), 4)
            doc.metadata["rerank_score"] = round(float(rerank_score), 4)
            scored_docs.append((doc, rerank_score))

        scored_docs.sort(key=lambda item: item[1], reverse=True)
        docs = [doc for doc, _ in scored_docs[: self.top_k]]
        logger.info(
            f"RAG检索完成，原始query={query}，扩展query={expanded_query}，候选数={len(candidates)}，入选数={len(docs)}"
        )
        return docs

    @staticmethod
    def _format_references(docs) -> str:
        """把命中的来源整理成回答尾部可展示的引用列表。"""
        references = []
        seen = set()
        for doc in docs:
            source = doc.metadata.get("source", "未知来源")
            page = doc.metadata.get("page")
            ref = f"{source} 第{page + 1}页" if isinstance(page, int) else source
            if ref not in seen:
                seen.add(ref)
                references.append(ref)
        if not references:
            return ""
        return "\n参考来源：\n- " + "\n- ".join(references)

    def rag_summarize(self, query: str, chat_history: str = ""):
        """对外暴露的 RAG 总入口，返回”总结结果 + 引用来源”。"""
        try:
            context_docs = self.retriever_docs(query, chat_history)
        except Exception as e:
            logger.error(f"RAG检索流程异常: {str(e)}", exc_info=True)
            return "知识库检索暂时不可用,请稍后重试."

        if not context_docs:
            return "未检索到相关参考资料。"

        # 把命中文档拼成可追踪来源的上下文，便于模型总结时引用。
        context_parts = []
        for counter, doc in enumerate(context_docs, start=1):
            source = doc.metadata.get("source", "未知来源")
            page = doc.metadata.get("page")
            chunk_index = doc.metadata.get("chunk_index")
            location_parts = [f"来源={source}"]
            if page is not None:
                location_parts.append(f"页码={page}")
            if chunk_index is not None:
                location_parts.append(f"切片={chunk_index}")
            context_parts.append(
                f"[参考资料{counter}] {' | '.join(location_parts)}\n{doc.page_content.strip()}"
            )
        context = "\n\n".join(context_parts)
        try:
            answer = self.chain.invoke(
                {
                    "input": query,
                    "context": context,
                }
            )
            answer = answer.strip()

            # 检查 LLM 返回是否表明知识库内容不足
            no_answer_keywords = ['未检索到相关参考资料', '完全不相关', '完全无法回答', '没有可用信息', '抱歉无法回答', '暂无相关数据']
            is_no_answer = any(kw in answer for kw in no_answer_keywords)

            if is_no_answer:
                # LLM 表示知识库内容不足，触发 Coze fallback
                logger.info(f"LLM 判断知识库内容不足，触发 Coze fallback")
                return self._fallback_to_external_api(query, chat_history, f"知识库内容不足：{answer[:50]}")

            return answer + self._format_references(context_docs)
        except Exception as e:
            logger.error(f"RAG总结失败: {str(e)}", exc_info=True)
            return "知识总结暂时不可用，请稍后重试。"


if __name__ == '__main__':
    rag = RagSummarizeService()
    print(rag.rag_summarize("小户型适合什么扫地机器人"))
