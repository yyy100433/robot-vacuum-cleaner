"""
知识库管理服务：提供完整的增删改查和分页功能
"""
import os
import json
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime

from utils.config_handler import chroma_conf
from utils.file_handler import (
    clean_text,
    get_file_md5_hex,
    listdir_with_allowed_type,
    normalize_documents,
    pdf_loader,
    txt_loader,
)
from utils.logger_handler import logger
from utils.path_tool import get_abs_path
from .vector_store import VectorStoreService, ParagraphSentenceTextSplitter


@dataclass
class KnowledgeChunk:
    """知识切片数据模型"""
    id: str
    content: str
    source: str
    source_type: str
    chunk_index: int
    relevance_score: float = 0.0
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeChunk":
        return cls(**data)


@dataclass
class KnowledgeFile:
    """知识文件元数据"""
    filename: str
    file_path: str
    file_size: int
    md5: str
    chunk_count: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeFile":
        return cls(**data)


@dataclass
class PaginatedResult:
    """分页结果"""
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() if hasattr(item, 'to_dict') else item for item in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
        }


class KnowledgeBaseService:
    """知识库管理服务，提供完整的 CRUD 和分页功能"""

    # 分页配置常量
    DEFAULT_PAGE_SIZE = 10
    MAX_PAGE_SIZE = 100
    MIN_PAGE_SIZE = 5

    def __init__(self):
        """初始化服务"""
        self.vector_store_service = VectorStoreService()
        self.kb_metadata_path = get_abs_path(chroma_conf.get('kb_metadata_path',
                    os.path.join(chroma_conf['persist_directory'], 'kb_metadata.json')))
        self._ensure_metadata_exists()

    def _ensure_metadata_exists(self):
        """确保元数据文件存在"""
        if not os.path.exists(self.kb_metadata_path):
            self._save_metadata({"files": {}, "chunks": {}})
            logger.info(f"初始化知识库元数据文件：{self.kb_metadata_path}")

    def _load_metadata(self) -> dict:
        """加载知识库元数据"""
        try:
            with open(self.kb_metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载知识库元数据失败：{e}，使用空元数据")
            return {"files": {}, "chunks": {}}

    def _save_metadata(self, metadata: dict):
        """保存知识库元数据"""
        try:
            os.makedirs(os.path.dirname(self.kb_metadata_path), exist_ok=True)
            with open(self.kb_metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存知识库元数据失败：{e}")
            raise

    # ==================== 知识库列表查询（带分页） ====================

    def list_files(
        self,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        keyword: str = "",
        file_type: str = ""
    ) -> PaginatedResult:
        """
        分页获取知识库文件列表

        Args:
            page: 页码，从 1 开始
            page_size: 每页数量
            keyword: 关键字过滤（文件名）
            file_type: 文件类型过滤（txt/pdf）

        Returns:
            分页结果
        """
        # 参数校验
        page = max(1, page)
        page_size = min(max(self.MIN_PAGE_SIZE, page_size), self.MAX_PAGE_SIZE)

        # 从 data 目录扫描实际文件
        data_path = get_abs_path(chroma_conf["data_path"])
        allowed_types = tuple(chroma_conf.get("allow_knowledge_file_type", [".txt", ".pdf"]))
        actual_files = listdir_with_allowed_type(data_path, allowed_types)

        metadata = self._load_metadata()
        meta_files = metadata.get("files", {})

        # 构建文件列表，优先使用实际文件信息
        all_files = []
        for file_path in actual_files:
            filename = os.path.basename(file_path)
            try:
                file_size = os.path.getsize(file_path)
                md5 = get_file_md5_hex(file_path)
            except Exception:
                file_size = 0
                md5 = ""

            # 获取元数据中的信息（如果有）
            meta_info = meta_files.get(filename, {})

            all_files.append(KnowledgeFile(
                filename=filename,
                file_path=file_path,
                file_size=file_size,
                md5=md5,
                chunk_count=meta_info.get("chunk_count", 0),
                created_at=meta_info.get("created_at", ""),
                updated_at=meta_info.get("updated_at", ""),
            ))

        # 应用过滤条件
        filtered_files = []
        for f in all_files:
            # 关键字过滤
            if keyword and keyword.lower() not in f.filename.lower():
                continue
            # 类型过滤
            if file_type and not f.filename.endswith(f".{file_type.lower()}"):
                continue
            filtered_files.append(f)

        # 计算总数和分页
        total = len(filtered_files)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_items = filtered_files[start_idx:end_idx]

        return PaginatedResult(
            items=paginated_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )

    # ==================== 单个知识文件详情 ====================

    def get_file_detail(self, filename: str) -> Optional[KnowledgeFile]:
        """获取单个文件的详细信息"""
        metadata = self._load_metadata()
        file_info = metadata.get("files", {}).get(filename)
        if file_info:
            return KnowledgeFile.from_dict(file_info)
        return None

    # ==================== 知识切片查询（带分页） ====================

    def list_chunks(
        self,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        source: str = "",
        source_type: str = "",
        keyword: str = ""
    ) -> PaginatedResult:
        """
        分页获取知识切片列表

        Args:
            page: 页码
            page_size: 每页数量
            source: 来源文件过滤
            source_type: 来源类型过滤
            keyword: 内容关键字搜索

        Returns:
            分页结果
        """
        # 参数校验
        page = max(1, page)
        page_size = min(max(self.MIN_PAGE_SIZE, page_size), self.MAX_PAGE_SIZE)

        metadata = self._load_metadata()
        chunks = metadata.get("chunks", {})

        # 应用过滤条件
        filtered_chunks = []
        for chunk_id, chunk_info in chunks.items():
            # 来源过滤
            if source and chunk_info.get("source") != source:
                continue
            # 类型过滤
            if source_type and chunk_info.get("source_type") != source_type:
                continue
            # 关键字搜索
            if keyword:
                content = chunk_info.get("content", "")
                if keyword.lower() not in content.lower():
                    continue
            filtered_chunks.append(KnowledgeChunk.from_dict(chunk_info))

        # 计算总数和分页
        total = len(filtered_chunks)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_items = filtered_chunks[start_idx:end_idx]

        return PaginatedResult(
            items=paginated_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )

    def get_chunk_detail(self, chunk_id: str) -> Optional[KnowledgeChunk]:
        """获取单个切片的详细信息"""
        metadata = self._load_metadata()
        chunk_info = metadata.get("chunks", {}).get(chunk_id)
        if chunk_info:
            return KnowledgeChunk.from_dict(chunk_info)
        return None

    # ==================== 添加知识库 ====================

    def add_file(
        self,
        file_path: str,
        force_reload: bool = False
    ) -> Tuple[bool, str]:
        """
        添加文件到知识库

        Args:
            file_path: 文件路径
            force_reload: 是否强制重新加载

        Returns:
            (success, message)
        """
        try:
            if not os.path.exists(file_path):
                return False, f"文件不存在：{file_path}"

            # 获取相对路径
            data_path = get_abs_path(chroma_conf["data_path"])
            relative_path = os.path.relpath(file_path, data_path)

            # 检查是否已存在且无需重新加载
            if not force_reload:
                existing = self._check_file_changed(relative_path)
                if existing:
                    return False, f"文件未变化，无需重复添加：{relative_path}"

            # 加载文档
            documents = self._load_document_by_path(file_path)
            if not documents:
                return False, f"无法加载文件内容：{file_path}"

            # 切分文档
            splitter = self._get_splitter_by_path(file_path)
            split_docs = splitter.split_documents(normalize_documents(documents))

            if not split_docs:
                return False, f"文件未被切分成任何文档：{file_path}"

            # 添加向量索引
            self._add_to_vector_store(relative_path, split_docs)

            # 更新元数据
            self._update_file_metadata(relative_path, split_docs)

            logger.info(f"成功添加文件到知识库：{relative_path}")
            return True, f"成功添加 {len(split_docs)} 个知识片段"

        except Exception as e:
            logger.error(f"添加文件到知识库失败：{e}", exc_info=True)
            return False, f"添加失败：{str(e)}"

    def add_text_content(
        self,
        content: str,
        title: str = "手动输入内容",
        source_type: str = "text"
    ) -> Tuple[bool, str]:
        """
        直接添加文本内容到知识库

        Args:
            content: 文本内容
            title: 标题（用作文件名）
            source_type: 来源类型

        Returns:
            (success, message)
        """
        try:
            if not content or not content.strip():
                return False, "内容不能为空"

            # 生成临时文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{title}_{timestamp}.txt"

            # 切分内容
            splitter = ParagraphSentenceTextSplitter(
                chunk_size=chroma_conf['chunk_size'],
                chunk_overlap=chroma_conf['chunk_overlap'],
            )
            from langchain_core.documents import Document
            docs = [Document(page_content=clean_text(content))]
            split_docs = splitter.split_documents(docs)

            # 添加到向量库
            self._add_to_vector_store(filename, split_docs, source_type=source_type)

            # 更新元数据
            self._update_file_metadata(filename, split_docs, is_manual=True)

            logger.info(f"成功添加手动文本到知识库：{filename}")
            return True, f"成功添加 {len(split_docs)} 个知识片段"

        except Exception as e:
            logger.error(f"添加手动文本失败：{e}", exc_info=True)
            return False, f"添加失败：{str(e)}"

    # ==================== 更新知识库 ====================

    def update_file(self, filename: str, new_content: str) -> Tuple[bool, str]:
        """
        更新知识库中的文本内容

        Args:
            filename: 文件名
            new_content: 新的内容

        Returns:
            (success, message)
        """
        # 先删除旧记录
        success, msg = self.delete_chunk_by_source(filename)
        if not success:
            return False, f"更新失败，无法删除旧内容：{msg}"

        # 再添加新内容
        return self.add_text_content(new_content, title=filename.replace(".txt", ""))

    # ==================== 删除知识库 ====================

    def delete_file(self, filename: str) -> Tuple[bool, str]:
        """
        删除整个文件的所有切片（包括物理文件）

        Args:
            filename: 文件名

        Returns:
            (success, message)
        """
        try:
            # 从向量库删除
            self.vector_store_service._delete_documents_by_source(filename)

            # 删除物理文件
            data_path = get_abs_path(chroma_conf["data_path"])
            file_path = os.path.join(data_path, filename)

            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"已删除物理文件：{file_path}")

            # 更新元数据
            metadata = self._load_metadata()
            files = metadata.get("files", {})
            chunks = metadata.get("chunks", {})

            # 删除文件记录
            if filename in files:
                del files[filename]

            # 删除该文件的所有切片
            chunks_to_delete = [
                cid for cid, cinfo in chunks.items()
                if cinfo.get("source") == filename
            ]
            for cid in chunks_to_delete:
                del chunks[cid]

            metadata["files"] = files
            metadata["chunks"] = chunks
            self._save_metadata(metadata)

            logger.info(f"成功删除文件：{filename}")
            return True, f"已删除文件 '{filename}' 及其所有切片"

        except Exception as e:
            logger.error(f"删除文件失败：{e}", exc_info=True)
            return False, f"删除失败：{str(e)}"

    def delete_chunk(self, chunk_id: str) -> Tuple[bool, str]:
        """
        删除单个切片

        Args:
            chunk_id: 切片 ID

        Returns:
            (success, message)
        """
        try:
            metadata = self._load_metadata()
            chunks = metadata.get("chunks", {})

            if chunk_id not in chunks:
                return False, f"切片不存在：{chunk_id}"

            # 获取源信息用于向量库删除
            chunk_info = chunks[chunk_id]
            source = chunk_info.get("source")

            # 从向量库删除
            self.vector_store_service.vector_store.delete(ids=[chunk_id])

            # 删除元数据
            del chunks[chunk_id]
            metadata["chunks"] = chunks
            self._save_metadata(metadata)

            logger.info(f"成功删除切片：{chunk_id}")
            return True, f"已删除切片"

        except Exception as e:
            logger.error(f"删除切片失败：{e}", exc_info=True)
            return False, f"删除失败：{str(e)}"

    def delete_chunk_by_source(self, source: str) -> Tuple[bool, str]:
        """按来源删除所有切片"""
        return self.delete_file(source)

    # ==================== 搜索知识库 ====================

    def search_chunks(
        self,
        query: str,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        k: int = 10
    ) -> PaginatedResult:
        """
        语义搜索知识切片

        Args:
            query: 搜索查询
            page: 页码
            page_size: 每页数量
            k: 返回最大数量

        Returns:
            分页结果
        """
        # 使用向量检索
        retriever = self.vector_store_service.get_retriever()
        retriever.search_kwargs["k"] = min(k, self.MAX_PAGE_SIZE)

        results = retriever.invoke(query)

        # 转换为 KnowledgeChunk
        chunks = []
        for doc in results:
            content_preview = doc.page_content[:50] if isinstance(doc.page_content, str) else ""
            chunk_id = f"{doc.metadata.get('source')}:{doc.metadata.get('chunk_index', 0)}:{hash(content_preview.encode('utf-8')) & 0xffffffff}"
            chunk = KnowledgeChunk(
                id=chunk_id,
                content=doc.page_content,
                source=doc.metadata.get("source", ""),
                source_type=doc.metadata.get("source_type", ""),
                chunk_index=doc.metadata.get("chunk_index", 0),
                relevance_score=doc.metadata.get("relevance_score", 0.0),
            )
            chunks.append(chunk)

        # 分页
        total = len(chunks)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_items = chunks[start_idx:end_idx]

        return PaginatedResult(
            items=paginated_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )

    # ==================== 内部辅助方法 ====================

    def _load_document_by_path(self, path: str):
        """根据文件路径加载文档"""
        if path.endswith(".txt"):
            return txt_loader(path)
        elif path.endswith(".pdf"):
            return pdf_loader(path)
        return None

    def _get_splitter_by_path(self, path: str):
        """根据文件路径获取对应的切分器"""
        if path.endswith(".txt"):
            return ParagraphSentenceTextSplitter(
                chunk_size=chroma_conf.get('txt_chunk_size', chroma_conf['chunk_size']),
                chunk_overlap=chroma_conf.get('txt_chunk_overlap', chroma_conf['chunk_overlap']),
            )
        elif path.endswith(".pdf"):
            return ParagraphSentenceTextSplitter(
                chunk_size=chroma_conf.get('pdf_chunk_size', chroma_conf['chunk_size']),
                chunk_overlap=chroma_conf.get('pdf_chunk_overlap', chroma_conf['chunk_overlap']),
            )
        return ParagraphSentenceTextSplitter(
            chunk_size=chroma_conf['chunk_size'],
            chunk_overlap=chroma_conf['chunk_overlap'],
        )

    def _check_file_changed(self, relative_path: str) -> bool:
        """检查文件是否发生变化"""
        metadata = self._load_metadata()
        files = metadata.get("files", {})

        file_info = files.get(relative_path)
        if not file_info:
            return False

        current_md5 = get_file_md5_hex(get_abs_path(f"data/{relative_path}"))
        return current_md5 == file_info.get("md5", "")

    def _build_chunk_id(self, source: str, chunk_index: int, content: str) -> str:
        """构建稳定的切片 ID"""
        digest = hash(content.encode("utf-8")) & 0xffffffff
        return f"{source}:{chunk_index}:{digest}"

    def _add_to_vector_store(self, source: str, docs, source_type: str = ""):
        """添加到向量数据库并更新元数据"""
        # 清理旧的切片
        self.vector_store_service._delete_documents_by_source(source)

        # 准备数据和 ID
        ids = []
        for index, doc in enumerate(docs):
            doc.metadata["source"] = source
            doc.metadata["source_type"] = source_type or os.path.splitext(source)[1].lstrip(".")
            ids.append(self._build_chunk_id(source, index, doc.page_content))

        # 批量添加
        batch_size = 10
        for i in range(0, len(docs), batch_size):
            self.vector_store_service.vector_store.add_documents(
                docs[i:i + batch_size],
                ids=ids[i:i + batch_size],
            )

        # 更新元数据
        self._update_chunks_metadata(source, docs)

    def _update_file_metadata(self, source: str, docs, is_manual: bool = False):
        """更新文件元数据"""
        metadata = self._load_metadata()
        files = metadata.get("files", {})

        file_path = get_abs_path(f"data/{source}") if not is_manual else ""
        file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0
        md5 = get_file_md5_hex(file_path) if file_path else ""

        files[source] = KnowledgeFile(
            filename=source,
            file_path=file_path,
            file_size=file_size,
            md5=md5,
            chunk_count=len(docs),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        ).to_dict()

        metadata["files"] = files
        self._save_metadata(metadata)

    def _update_chunks_metadata(self, source: str, docs):
        """更新切片元数据"""
        metadata = self._load_metadata()
        chunks = metadata.get("chunks", {})

        # 删除该来源的旧切片
        chunks_to_remove = [
            cid for cid in chunks.keys()
            if cid.startswith(f"{source}:")
        ]
        for cid in chunks_to_remove:
            del chunks[cid]

        # 添加新切片
        for index, doc in enumerate(docs):
            chunk_id = self._build_chunk_id(source, index, doc.page_content)
            chunks[chunk_id] = KnowledgeChunk(
                id=chunk_id,
                content=doc.page_content,
                source=source,
                source_type=doc.metadata.get("source_type", ""),
                chunk_index=index,
                created_at=datetime.now().isoformat(),
            ).to_dict()

        metadata["chunks"] = chunks
        self._save_metadata(metadata)
