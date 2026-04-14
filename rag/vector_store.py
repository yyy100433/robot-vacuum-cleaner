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

# 确保 Settings 使用最低的遥测配置
def _create_settings_with_no_telemetry():
    """创建一个完全禁用遥测的 Settings 对象"""
    return Settings(
        anonymized_telemetry=False,
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
from utils.config_handler import chroma_conf
from model.factory import get_embedding_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
import hashlib
import json
import os
import shutil
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
        self.default_splitter = self._build_splitter(
            chunk_size=chroma_conf['chunk_size'],
            chunk_overlap=chroma_conf['chunk_overlap'],
        )
        self.txt_splitter = self._build_splitter(
            chunk_size=chroma_conf.get('txt_chunk_size', chroma_conf['chunk_size']),
            chunk_overlap=chroma_conf.get('txt_chunk_overlap', chroma_conf['chunk_overlap']),
        )
        self.pdf_splitter = self._build_splitter(
            chunk_size=chroma_conf.get('pdf_chunk_size', chroma_conf['chunk_size']),
            chunk_overlap=chroma_conf.get('pdf_chunk_overlap', chroma_conf['chunk_overlap']),
        )

    def _build_splitter(self, chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
        """按给定参数构造切块器，便于 txt/pdf 分别调参。"""
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=chroma_conf['separators'],
            length_function=len,
        )

    def _get_splitter(self, read_path: str) -> RecursiveCharacterTextSplitter:
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
            # 如果创建失败，尝试清理并重建
            try:
                if os.path.exists(self.persist_directory):
                    shutil.rmtree(self.persist_directory)
                os.makedirs(self.persist_directory, exist_ok=True)
                return Chroma(
                    collection_name=self.collection_name,
                    embedding_function=get_embedding_model(),
                    persist_directory=self.persist_directory,
                    client_settings=_create_settings_with_no_telemetry(),
                )
            except Exception as e2:
                logger.error(f"重建向量库失败: {str(e2)}")
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
