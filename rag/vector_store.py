# 必须在导入 chromadb 之前设置环境变量并完全禁用遥测
import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_DISABLE_TELEMETRY"] = "True"
os.environ["CHROMA_SERVER_NOLOG"] = "True"

import sys

# 在最早期禁用 posthog 网络连接
class _DisabledPosthog:
    """禁用的 Posthog 客户端，阻止任何网络请求"""
    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def capture(user_id, event_name=None, properties=None):
        """必须接受 3 个参数，因为 chromadb 调用 posthog.capture(user_id, event_name, properties)"""
        return None

    @staticmethod
    def identify(*args, **kwargs):
        return None

    @staticmethod
    def flush():
        return None

    # 直接设置为 True 禁用
    disabled = True
    project_api_key = ""

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        @staticmethod
        def capture(user_id, event_name=None, properties=None):
            return None

        @staticmethod
        def identify(*args, **kwargs):
            return None

        @staticmethod
        def flush():
            return None

def _disable_telemetry_early():
    """在最早期禁用遥测，防止任何 capture() 调用和网络连接"""

    # 预注册假模块
    fake_ph = _DisabledPosthog()
    sys.modules['fake_posthog'] = fake_ph

    # Patch 已加载的模块
    modules_to_patch = [
        'posthog',
        'chromadb',
        'chromadb.telemetry',
        'chromadb.telemetry.posthog',
        'chromadb.api.types',
        'chromadb.server.fastapi',
        'chromadb.db.mixtelemetry',
    ]

    for mod_name in modules_to_patch:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            # Patch capture 函数 - 确保签名匹配 chromadb 的调用
            if hasattr(mod, 'capture'):
                setattr(mod, 'capture', fake_ph.capture)
            if hasattr(mod, 'TelemetryClient'):
                setattr(mod, 'TelemetryClient', _DisabledPosthog.Client)
            if hasattr(mod, 'PosthogClient'):
                setattr(mod, 'PosthogClient', _DisabledPosthog.Client)
            # 直接设置 disabled=True
            if hasattr(mod, 'disabled'):
                setattr(mod, 'disabled', True)

    # 尝试导入并 patch posthog
    try:
        import posthog
        posthog.capture = fake_ph.capture
        posthog.identify = fake_ph.identify
        posthog.flush = fake_ph.flush
        posthog.disabled = True
        if hasattr(posthog, 'Client'):
            posthog.Client = _DisabledPosthog.Client
        if hasattr(posthog, 'Posthog'):
            posthog.Posthog = _DisabledPosthog
    except Exception:
        pass

    # 尝试导入并 patch chromadb telemetry
    try:
        import chromadb.telemetry.posthog as cpposthog
        cpposthog.capture = fake_ph.capture
        if hasattr(cpposthog, 'PosthogClient'):
            cpposthog.PosthogClient = _DisabledPosthog.Client
        if hasattr(cpposthog, 'TelemetryClient'):
            cpposthog.TelemetryClient = _DisabledPosthog.Client
    except Exception:
        pass

    # 尝试直接替换 chromadb 核心模块的 capture 方法
    try:
        import chromadb
        # 查找并替换所有可能的 capture 方法
        for attr_name in dir(chromadb):
            attr = getattr(chromadb, attr_name, None)
            if attr and hasattr(attr, 'capture'):
                try:
                    setattr(attr, 'capture', lambda self, user_id, event_name=None, properties=None: None)
                except Exception:
                    pass
    except Exception:
        pass

_disable_telemetry_early()

from langchain_chroma import Chroma
from chromadb.config import Settings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.config_handler import chroma_conf
from model.factory import get_embedding_model
from utils.path_tool import get_abs_path
import hashlib
import json
import os
import shutil
import re
from datetime import datetime
from utils.file_handler import (
    clean_text,
    get_file_md5_hex,
    listdir_with_allowed_type,
    normalize_documents,
    pdf_loader,
    split_qa_documents,
    txt_loader,
)
from utils.logger_handler import logger


# 确保 Settings 使用最低的遥测配置
def _create_settings_with_no_telemetry():
    """创建一个完全禁用遥测的 Settings 对象"""
    return Settings(
        anonymized_telemetry=False,
        allow_reset=True,
        is_persistent=True,
    )


# 导入后再次确保 Chroma 类的 _telemetry_client 被禁用
def _patch_chroma_telemetry():
    """Patch Chroma class to disable telemetry client"""
    try:
        if hasattr(Chroma, '_telemetry_client'):
            Chroma._telemetry_client = None
    except Exception:
        pass


_patch_chroma_telemetry()


class ParagraphSentenceTextSplitter(RecursiveCharacterTextSplitter):
    """基于段落和完整句子的文本切分器。

    切分优先级：
    1. 先按段落切分（双换行符 \n\n）
    2. 如果段落过长，再按完整句子切分（句号、问号、感叹号等）
    3. 确保句子完整性，不会在句子中间切断
    """

    def __init__(
        self,
        chunk_size: int = 400,
        chunk_overlap: int = 50,
        length_function=len,
        **kwargs
    ):
        # 定义切分优先级：段落 > 句子 > 短语 > 词 > 字符
        separators = [
            "\n\n",      # 段落分隔（最高优先级）
            "\n",        # 行分隔
            "。",        # 中文句号
            "？",        # 中文问号
            "！",        # 中文感叹号
            "；",        # 中文分号
            ".",         # 英文句号
            "?",         # 英文问号
            "!",         # 英文感叹号
            ";",         # 英文分号
            "，",        # 中文逗号
            ",",         # 英文逗号
            " ",         # 空格
            "",          # 最后按字符切分
        ]
        super().__init__(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
            length_function=length_function,
            **kwargs
        )

    def split_text(self, text: str) -> list[str]:
        """按段落和句子切分文本，确保句子完整性。"""
        # 先清理文本
        text = self._clean_text_for_split(text)

        # 第一步：按段落切分
        paragraphs = self._split_by_paragraphs(text)

        # 第二步：对过长的段落按句子切分
        chunks = []
        for paragraph in paragraphs:
            if len(paragraph) <= self._chunk_size:
                chunks.append(paragraph)
            else:
                # 段落过长，按句子切分并合并
                sentence_chunks = self._split_long_paragraph(paragraph)
                chunks.extend(sentence_chunks)

        # 第三步：合并过短的相邻块，避免碎片化
        final_chunks = self._merge_short_chunks(chunks)

        return final_chunks

    def _clean_text_for_split(self, text: str) -> str:
        """清理文本，统一换行格式。"""
        # 统一换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # 去除多余空白
        text = re.sub(r"[ \t]+", " ", text)
        # 确保段落分隔符是双换行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _split_by_paragraphs(self, text: str) -> list[str]:
        """按双换行符切分段落。"""
        paragraphs = text.split("\n\n")
        # 清理每个段落并过滤空段落
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        """将过长的段落按完整句子切分，并智能合并。"""
        # 提取完整句子
        sentences = self._extract_sentences(paragraph)

        # 按chunk_size合并句子
        chunks = []
        current_chunk = ""

        for sentence in sentences:
            # 如果单个句子就超过chunk_size，需要进一步切分
            if len(sentence) > self._chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # 对超长句子按分隔符递归切分
                super_long_chunks = super().split_text(sentence)
                chunks.extend(super_long_chunks)
            elif len(current_chunk) + len(sentence) <= self._chunk_size:
                current_chunk += sentence
            else:
                # 当前块已满，保存并开始新块
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence

        # 保存最后一个块
        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def _extract_sentences(self, text: str) -> list[str]:
        """提取完整句子，保留句子结尾的标点符号。"""
        # 匹配中文和英文句子结尾（包括可能的引号）
        sentence_endings = re.compile(r'([。？！；.?!;]+["\'」』]?)')

        # 在句子结尾后插入分隔标记
        text_with_markers = sentence_endings.sub(r'\1<SPLIT>', text)

        # 按标记切分
        raw_sentences = text_with_markers.split('<SPLIT>')

        # 清理并保留非空句子
        sentences = []
        for s in raw_sentences:
            s = s.strip()
            if s:
                sentences.append(s)

        return sentences

    def _merge_short_chunks(self, chunks: list[str]) -> list[str]:
        """合并过短的相邻块，避免碎片化。"""
        min_chunk_size = 50  # 最小块大小，低于此值尝试合并

        merged = []
        i = 0

        while i < len(chunks):
            current = chunks[i]

            # 如果当前块太短且有下一块，尝试合并
            while (
                i + 1 < len(chunks)
                and len(current) < min_chunk_size
                and len(current) + len(chunks[i + 1]) <= self._chunk_size
            ):
                i += 1
                current = current + "\n" + chunks[i]

            merged.append(current)
            i += 1

        return merged


class VectorStoreService:
    """知识库入库与向量库维护服务。"""

    def __init__(self):
        """初始化路径、manifest 与不同文件类型的切块器。"""
        self.collection_name = chroma_conf['collection_name']
        self.persist_directory = get_abs_path(chroma_conf['persist_directory'])
        self.md5_hex_store = get_abs_path(chroma_conf['md5_hex_store'])
        self.manifest_store = get_abs_path(
            chroma_conf.get('manifest_store', os.path.join(self.persist_directory, 'knowledge_manifest.json'))
        )
        os.makedirs(self.persist_directory, exist_ok=True)
        manifest_dir = os.path.dirname(self.manifest_store)
        if manifest_dir:
            os.makedirs(manifest_dir, exist_ok=True)
        self.vector_store = self._create_vector_store()
        # 使用新的段落-句子切分器
        self.default_splitter = ParagraphSentenceTextSplitter(
            chunk_size=chroma_conf['chunk_size'],
            chunk_overlap=chroma_conf['chunk_overlap'],
        )
        self.txt_splitter = ParagraphSentenceTextSplitter(
            chunk_size=chroma_conf.get('txt_chunk_size', chroma_conf['chunk_size']),
            chunk_overlap=chroma_conf.get('txt_chunk_overlap', chroma_conf['chunk_overlap']),
        )
        self.pdf_splitter = ParagraphSentenceTextSplitter(
            chunk_size=chroma_conf.get('pdf_chunk_size', chroma_conf['chunk_size']),
            chunk_overlap=chroma_conf.get('pdf_chunk_overlap', chroma_conf['chunk_overlap']),
        )

    def _get_splitter(self, read_path: str):
        """根据文件类型选择更合适的切块器。"""
        if read_path.endswith(".txt"):
            return self.txt_splitter
        if read_path.endswith(".pdf"):
            return self.pdf_splitter
        return self.default_splitter

    def _create_vector_store(self):
        """创建 Chroma 客户端实例，带容错处理。"""
        try:
            return Chroma(
                collection_name=self.collection_name,
                embedding_function=get_embedding_model(),
                persist_directory=self.persist_directory,
                client_settings=_create_settings_with_no_telemetry(),
            )
        except Exception as e:
            logger.warning(f"创建向量库失败，尝试重建: {str(e)}")
            return self._rebuild_vector_store()

    def _rebuild_vector_store(self):
        """清理并重建向量库。"""
        try:
            # 确保关闭所有可能占用文件的句柄
            import gc
            gc.collect()

            # 彻底删除整个向量库目录
            if os.path.exists(self.persist_directory):
                try:
                    shutil.rmtree(self.persist_directory)
                    logger.info(f"已删除损坏的向量库目录: {self.persist_directory}")
                except Exception as e:
                    logger.warning(f"删除向量库目录失败: {str(e)}")
                    # 尝试强制删除
                    import time
                    time.sleep(0.5)
                    if os.path.exists(self.persist_directory):
                        shutil.rmtree(self.persist_directory, ignore_errors=True)

            # 重新创建目录
            os.makedirs(self.persist_directory, exist_ok=True)

            # 清理 manifest 文件
            if os.path.exists(self.manifest_store):
                try:
                    os.remove(self.manifest_store)
                    logger.info(f"已删除 manifest: {self.manifest_store}")
                except Exception as e:
                    logger.warning(f"删除 manifest 失败: {str(e)}")

            # 清理旧版 md5 文件
            if os.path.exists(self.md5_hex_store):
                try:
                    os.remove(self.md5_hex_store)
                    logger.info(f"已删除 md5 文件: {self.md5_hex_store}")
                except Exception as e:
                    logger.warning(f"删除 md5 文件失败: {str(e)}")

            # 重新创建 Chroma 实例
            logger.info("正在重建向量库...")
            return Chroma(
                collection_name=self.collection_name,
                embedding_function=get_embedding_model(),
                persist_directory=self.persist_directory,
                client_settings=_create_settings_with_no_telemetry(),
            )
        except Exception as e2:
            logger.error(f"重建向量库失败: {str(e2)}", exc_info=True)
            raise

    def get_retriever(self):
        return self.vector_store.as_retriever(search_kwargs={"k": chroma_conf['k']})

    def get_collection_count(self):
        return self.vector_store._collection.count()

    def _load_manifest(self) -> dict:
        """读取知识库 manifest，用于增量同步。"""
        if not os.path.exists(self.manifest_store):
            return {}
        try:
            with open(self.manifest_store, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"读取知识库 manifest 失败，将按空 manifest 处理: {str(e)}")
            return {}

    def _save_manifest(self, manifest: dict):
        """落盘 manifest，记录每个来源文件的当前状态。"""
        with open(self.manifest_store, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def _manifest_item(md5_hex: str, chunk_count: int) -> dict:
        """构造单个知识文件的 manifest 记录。"""
        return {
            "md5": md5_hex,
            "chunk_count": chunk_count,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _sync_manifest_from_legacy_md5(self, manifest: dict):
        """兼容旧版 md5.txt，避免升级后全部文件被重复视为新文件。"""
        if manifest or not os.path.exists(self.md5_hex_store):
            return manifest
        try:
            with open(self.md5_hex_store, "r", encoding="utf-8") as f:
                legacy_md5s = {line.strip() for line in f if line.strip()}
        except Exception as e:
            logger.warning(f"读取旧 md5 文件失败，跳过兼容迁移: {str(e)}")
            return manifest

        allowed_files_path = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"])
        )
        for path in allowed_files_path:
            md5_hex = get_file_md5_hex(path)
            if md5_hex in legacy_md5s:
                relative_source = os.path.relpath(path, get_abs_path(chroma_conf["data_path"]))
                manifest.setdefault(relative_source, self._manifest_item(md5_hex, 0))
        if manifest:
            self._save_manifest(manifest)
        return manifest

    @staticmethod
    def _build_chunk_id(source: str, chunk_index: int, content: str) -> str:
        """基于来源、位置和内容哈希生成稳定 chunk ID。"""
        digest = hashlib.md5(content.encode("utf-8")).hexdigest()[:12]
        return f"{source}:{chunk_index}:{digest}"

    def _delete_documents_by_source(self, source: str):
        """按来源删除旧切片，保证文件更新时不会残留历史版本。"""
        try:
            self.vector_store.delete(where={"source": source})
        except Exception as e:
            logger.warning(f"按来源删除旧切片失败，source={source}, error={str(e)}")

    def _cleanup_stale_documents(self, allowed_files_path: tuple[str, ...]):
        """清理已经从 data 目录移除，但仍残留在向量库中的旧来源。"""
        existing_sources = {
            os.path.relpath(path, get_abs_path(chroma_conf["data_path"])) for path in allowed_files_path
        }
        manifest = self._sync_manifest_from_legacy_md5(self._load_manifest())
        try:
            stored = self.vector_store.get(include=["metadatas"])
        except Exception as e:
            logger.warning(f"读取向量库元数据失败，跳过陈旧切片清理: {str(e)}")
            stored = {"metadatas": []}

        stale_sources = set()
        for metadata in stored.get("metadatas", []):
            if not metadata:
                continue
            source = metadata.get("source")
            if source and source not in existing_sources:
                stale_sources.add(source)
        for source in list(manifest.keys()):
            if source not in existing_sources:
                stale_sources.add(source)

        for source in stale_sources:
            self._delete_documents_by_source(source)
            manifest.pop(source, None)
            logger.info(f"已清理已删除知识文件遗留的切片: {source}")
        self._save_manifest(manifest)

    def reset_store(self, clear_md5=True):
        """
        重建本地向量库，处理索引文件与元数据不一致的问题。
        """
        try:
            self.vector_store.delete_collection()
        except Exception as e:
            logger.warning(f"删除旧向量集合失败，将继续重建目录: {str(e)}")

        try:
            if os.path.exists(self.persist_directory):
                shutil.rmtree(self.persist_directory)
        except Exception as e:
            logger.warning(f"删除向量库目录失败: {str(e)}")

        os.makedirs(self.persist_directory, exist_ok=True)

        if clear_md5 and os.path.exists(self.md5_hex_store):
            try:
                os.remove(self.md5_hex_store)
            except Exception as e:
                logger.warning(f"删除md5去重文件失败: {str(e)}")
        if os.path.exists(self.manifest_store):
            try:
                os.remove(self.manifest_store)
            except Exception as e:
                logger.warning(f"删除知识库 manifest 失败: {str(e)}")

        self.vector_store = self._create_vector_store()
        logger.info("向量库重建完成")

    def load_document(self, force_reload=False):
        """
        从数据文件读取内容存放到向量数据库，计算md5进行去重
        :return:
        """
        def get_file_document(read_path):
            """根据文件后缀选择对应的加载器。"""
            if read_path.endswith("txt"):
                return txt_loader(read_path)
            elif read_path.endswith("pdf"):
                return pdf_loader(read_path)
            return []

        allowed_files_path = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"])
        )
        self._cleanup_stale_documents(allowed_files_path)
        manifest = self._sync_manifest_from_legacy_md5(self._load_manifest())

        for path in allowed_files_path:
            md5_hex = get_file_md5_hex(path)
            relative_source = os.path.relpath(path, get_abs_path(chroma_conf["data_path"]))
            if not force_reload and manifest.get(relative_source, {}).get("md5") == md5_hex:
                logger.info(f"文件{path}未发生变化，跳过加载")
                continue
            try:
                documents = get_file_document(path)
                if not documents:
                    logger.warning(f"文件{path}没有加载到任何文档，可能是格式不受支持")
                    continue
                documents = normalize_documents(documents)
                # FAQ/100问类资料优先按问答结构拆分，比纯长度切块更稳定。
                if relative_source.endswith("100问.txt") or "常见问题" in clean_text(documents[0].page_content[:80]):
                    documents = split_qa_documents(documents)
                for document in documents:
                    document.metadata["source"] = relative_source
                    document.metadata["source_type"] = os.path.splitext(path)[1].lstrip(".").lower()
                split_document = self._get_splitter(path).split_documents(documents)
                if not split_document:
                    logger.warning(f"文件{path}没有被切分成任何文档，可能是内容过短或切分参数不合适")
                    continue
                self._delete_documents_by_source(relative_source)
                for index, document in enumerate(split_document):
                    document.metadata["chunk_index"] = index
                ids = [
                    self._build_chunk_id(
                        relative_source,
                        index,
                        document.page_content,
                    )
                    for index, document in enumerate(split_document)
                ]
                # DashScope 单次 embedding 批量上限较小，这里按批次写入更稳。
                batch_size = 10
                for i in range(0, len(split_document), batch_size):
                    self.vector_store.add_documents(
                        split_document[i:i + batch_size],
                        ids=ids[i:i + batch_size],
                    )
                manifest[relative_source] = self._manifest_item(md5_hex, len(split_document))
                self._save_manifest(manifest)
                logger.info(f"文件{path}已成功加载到向量数据库中")
            except Exception as e:
                # 这里保留 exc_info，方便定位具体是加载、切块还是写库阶段出错。
                logger.error(f"加载文件{path}到向量数据库失败: {str(e)}", exc_info=True)
                continue


if __name__ == '__main__':
    vs = VectorStoreService()
    vs.load_document()
    retriever = vs.get_retriever()
    res = retriever.invoke("迷路")
    for r in res:
        print(r.page_content)
