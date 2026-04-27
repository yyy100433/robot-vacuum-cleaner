import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_DISABLE_TELEMETRY"] = "True"
os.environ["CHROMA_SERVER_NOLOG"] = "True"

# 加载 .env 文件中的环境变量
from dotenv import load_dotenv
from utils.path_tool import get_abs_path
load_dotenv(get_abs_path(".env"))

# 在导入任何项目模块前 patch posthog，防止连接错误
import sys

class _DisabledPosthog:
    """禁用的 Posthog 客户端，阻止任何网络请求"""
    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def capture(user_id, event_name=None, properties=None):
        """必须接受 3 个参数，因为 chromadb 调用 posthog.capture(user_id, event_name, properties)"""
        return None

    disabled = True
    project_api_key = ""

    class Client:
        @staticmethod
        def capture(user_id, event_name=None, properties=None):
            return None

def _patch_telemetry():
    """Patch posthog 和 chromadb 遥测"""
    fake_ph = _DisabledPosthog()
    sys.modules['fake_posthog'] = fake_ph

    for mod_name in ['posthog', 'chromadb.telemetry.posthog']:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, 'capture'):
                setattr(mod, 'capture', fake_ph.capture)
            if hasattr(mod, 'PosthogClient'):
                setattr(mod, 'PosthogClient', _DisabledPosthog.Client)
            setattr(mod, 'disabled', True)

    try:
        import posthog
        posthog.capture = fake_ph.capture
        posthog.disabled = True
    except Exception:
        pass

    try:
        import chromadb.telemetry.posthog as cpp
        cpp.capture = fake_ph.capture
        if hasattr(cpp, 'PosthogClient'):
            cpp.PosthogClient = _DisabledPosthog.Client
    except Exception:
        pass

_patch_telemetry()

import streamlit as st
from agent.react_agent import ReactAgent
from agent.tools.agent_tools import rag as rag_service
from utils.bootstrap import validate_runtime
from utils.chat_session_store import (
    create_session,
    delete_session,
    load_sessions,
    save_sessions,
    sort_sessions,
    update_session_messages,
    upsert_session,
)
from utils.file_handler import clean_text, pdf_loader, txt_loader, get_file_md5_hex, listdir_with_allowed_type
from utils.logger_handler import logger
from utils.path_tool import get_abs_path
from utils.config_handler import chroma_conf
import re
import shutil
import hashlib
import os
from datetime import datetime


st.set_page_config(
    page_title="扫地机器人智能客服",
    page_icon="🤖",
    layout="wide",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700;800&display=swap');

    :root {
        --brand-ink: #19324a;
        --brand-cyan: #3da9a3;
        --brand-gold: #f2c26b;
        --bg-soft: #f5f8fa;
        --surface: #ffffff;
        --surface-2: #ecf3f7;
        --line: #d9e3ea;
        --text-main: #13293d;
        --text-muted: #2f4d63;
    }

    html, body, [class*="css"] {
        font-family: 'Noto Sans SC', sans-serif;
        color: var(--text-main);
        -webkit-font-smoothing: antialiased;
        text-rendering: optimizeLegibility;
    }

    .stApp {
        background:
            radial-gradient(1200px 420px at 100% -10%, rgba(61,169,163,0.22), transparent 70%),
            radial-gradient(900px 400px at -10% 5%, rgba(242,194,107,0.24), transparent 65%),
            linear-gradient(180deg, #f8fbfd 0%, #f2f6f9 100%);
    }

    .main .block-container {
        max-width: 980px;
        padding-top: 2rem;
        padding-bottom: 6.5rem;
    }

    .hero-wrap {
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 1rem 1.25rem;
        background: linear-gradient(150deg, rgba(255,255,255,0.94), rgba(236,243,247,0.92));
        box-shadow: 0 12px 30px rgba(25,50,74,0.08);
        animation: riseIn 0.45s ease-out;
    }

    .hero-title {
        margin: 0;
        color: var(--brand-ink);
        font-size: 1.95rem;
        font-weight: 800;
        letter-spacing: 0.2px;
    }

    .hero-sub {
        margin: 0.35rem 0 0;
        color: var(--text-muted);
        font-size: 1.06rem;
        font-weight: 600;
    }

    .stat {
        margin-top: 0.9rem;
        display: inline-block;
        padding: 0.38rem 0.72rem;
        border-radius: 999px;
        border: 1px solid #c5d7e3;
        background: #f8fcff;
        color: #1f3d54;
        font-size: 0.9rem;
        font-weight: 700;
        margin-right: 0.45rem;
    }

    div[data-testid="stChatMessage"] {
        border-radius: 16px;
        border: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.96);
        box-shadow: 0 7px 18px rgba(15,43,66,0.07);
        animation: riseIn 0.35s ease-out;
    }

    div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p,
    div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {
        font-size: 1.08rem;
        line-height: 1.86;
        color: var(--text-main);
        font-weight: 500;
    }

    div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h1,
    div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h2,
    div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h3 {
        color: #143248;
        letter-spacing: 0.2px;
        font-weight: 800;
    }

    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"] {
        transform: scale(1.08);
    }

    /* ========== 输入框样式重构 ========== */

    /* 最外层容器 - 白色框，固定在底部 */
    [data-testid="stChatInput"] {
        position: fixed !important;
        bottom: 1rem !important;
        width: min(680px, calc(100% - 2rem)) !important;
        max-width: 680px !important;
        left: 50% !important;
        transform: translateX(-50%) !important;
        background: rgba(255,255,255,0.98) !important;
        border: 1px solid var(--line) !important;
        border-radius: 16px !important;
        box-shadow: 0 12px 28px rgba(15,43,66,0.12) !important;
        padding: 0 !important;
        margin: 0 !important;
        overflow: hidden !important;
    }

    /* 隐藏 Streamlit 原生的所有内部边框和容器 */
    [data-testid="stChatInputContainer"],
    .stChatInputContainer,
    [data-testid="stChatInput"] > div,
    [data-testid="stChatInput"] section,
    [data-testid="stChatInput"] [data-testid="stVerticalBlock"],
    [data-testid="stChatInput"] [data-testid="stElementContainer"] {
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        background: transparent !important;
        padding: 0 !important;
        margin: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }

    /* 输入区域的包装器 - 确保不超出白色框 */
    [data-testid="stChatInput"] [data-testid="stChatInputTextAreaWrapper"] {
        border: none !important;
        outline: none !important;
        background: transparent !important;
        padding: 0.6rem 0.8rem !important;
        margin: 0 !important;
        width: calc(100% - 1.6rem) !important;
        max-width: calc(100% - 1.6rem) !important;
        overflow: hidden !important;
    }

    /* 文字输入区域 - 关键：限制宽度并自动换行 */
    [data-testid="stChatInput"] textarea {
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        color: var(--text-main) !important;
        border: none !important;
        outline: none !important;
        background: transparent !important;
        box-shadow: none !important;
        /* 关键：限制宽度 */
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        /* 关键：自动换行 */
        word-wrap: break-word !important;
        overflow-wrap: break-word !important;
        white-space: pre-wrap !important;
        /* 高度控制 */
        min-height: 28px !important;
        max-height: 100px !important;
        height: auto !important;
        resize: none !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        line-height: 1.6 !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    /* 隐藏所有 focus 状态的边框 */
    [data-testid="stChatInput"]:focus-within,
    [data-testid="stChatInput"] textarea:focus,
    [data-testid="stChatInput"] *:focus,
    [data-testid="stChatInputContainer"]:focus-within {
        outline: none !important;
        border: none !important;
        box-shadow: none !important;
    }

    /* 确保输入框内的所有元素都不超出 */
    [data-testid="stChatInput"] * {
        box-sizing: border-box !important;
        max-width: 100% !important;
    }

    /* 隐藏发送按钮区域的多余空间 */
    [data-testid="stChatInput"] [data-testid="stChatInputSubmitButton"] {
        padding: 0 !important;
        margin: 0 !important;
    }

    /* ========== 输入框样式结束 ========== */

    div[data-testid="stHorizontalBlock"] div[data-testid="column"] .stButton button {
        width: 100%;
        border-radius: 999px;
        border: 1px solid #c0d4e2;
        background: #f7fbfe;
        color: #1e3f57;
        font-weight: 700;
        font-size: 0.98rem;
        transition: all 0.18s ease;
    }

    div[data-testid="stHorizontalBlock"] div[data-testid="column"] .stButton button:hover {
        background: #e7f4f3;
        border-color: #7fbcb7;
        color: #1f4541;
    }

    div[data-testid="stAlert"] p {
        color: #163349;
        font-size: 1rem;
        font-weight: 600;
    }

    .ref-wrap {
        margin-top: 0.85rem;
        padding-top: 0.8rem;
        border-top: 1px dashed #c9d8e3;
    }

    .ref-title {
        margin-bottom: 0.45rem;
        color: #38586f;
        font-size: 0.88rem;
        font-weight: 800;
        letter-spacing: 0.04em;
    }

    .ref-chip {
        display: inline-block;
        margin: 0 0.45rem 0.45rem 0;
        padding: 0.32rem 0.62rem;
        border-radius: 999px;
        border: 1px solid #c6d9e6;
        background: #f4fafc;
        color: #214158;
        font-size: 0.86rem;
        font-weight: 700;
        line-height: 1.3;
    }

    .ref-preview {
        margin-top: 0.2rem;
        color: #28465b;
        font-size: 0.96rem;
        line-height: 1.75;
    }

    @keyframes riseIn {
        from {
            transform: translateY(8px);
            opacity: 0;
        }
        to {
            transform: translateY(0);
            opacity: 1;
        }
    }

    @media (max-width: 768px) {
        .main .block-container {
            padding-top: 1.2rem;
            padding-bottom: 7.2rem;
        }

        .hero-title {
            font-size: 1.58rem;
        }

        div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p,
        div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {
            font-size: 1rem;
            line-height: 1.78;
        }

        [data-testid="stChatInput"] {
            width: calc(100% - 0.9rem);
            bottom: 0.4rem;
            border-radius: 14px;
            padding: 0.2rem 0.5rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

runtime_issues = validate_runtime()
if runtime_issues:
    for issue in runtime_issues:
        st.error(issue)
    st.stop()

# Agent 实例只初始化一次，避免每次重跑页面都重新构建模型和工具。
if "agent" not in st.session_state:
    st.session_state["agent"] = ReactAgent()

# sessions 存的是全部历史会话；current_session_id 指向当前打开的那一个。
if "sessions" not in st.session_state:
    sessions = sort_sessions(load_sessions())
    if not sessions:
        sessions = [create_session()]
        save_sessions(sessions)
    st.session_state["sessions"] = sessions

if "current_session_id" not in st.session_state:
    st.session_state["current_session_id"] = st.session_state["sessions"][0]["id"]

if "pending_prompt" not in st.session_state:
    st.session_state["pending_prompt"] = ""


def get_current_session() -> dict:
    """根据 current_session_id 取当前会话；如果丢失则兜底创建一个新会话。"""
    current_session_id = st.session_state["current_session_id"]
    for session in st.session_state["sessions"]:
        if session["id"] == current_session_id:
            return session
    fallback = create_session()
    st.session_state["sessions"] = [fallback] + st.session_state["sessions"]
    st.session_state["current_session_id"] = fallback["id"]
    save_sessions(sort_sessions(st.session_state["sessions"]))
    return fallback


def persist_current_messages(messages: list[dict]) -> None:
    """把当前会话消息写回内存和本地文件。"""
    current = get_current_session()
    updated = update_session_messages(current, messages)
    st.session_state["sessions"] = sort_sessions(upsert_session(st.session_state["sessions"], updated))
    st.session_state["current_session_id"] = updated["id"]
    save_sessions(st.session_state["sessions"])


def switch_session(session_id: str) -> None:
    """切换当前会话，同时清空待发送的快捷问题。"""
    st.session_state["current_session_id"] = session_id
    st.session_state["pending_prompt"] = ""


def create_new_chat() -> None:
    """创建新会话并立即切过去。"""
    new_session = create_session()
    st.session_state["sessions"] = sort_sessions(upsert_session(st.session_state["sessions"], new_session))
    st.session_state["current_session_id"] = new_session["id"]
    st.session_state["pending_prompt"] = ""
    save_sessions(st.session_state["sessions"])


def delete_current_chat() -> None:
    """删除当前会话；如果删完为空，则自动补一个空会话。"""
    current_id = st.session_state["current_session_id"]
    sessions = delete_session(st.session_state["sessions"], current_id)
    if not sessions:
        sessions = [create_session()]
    sessions = sort_sessions(sessions)
    st.session_state["sessions"] = sessions
    st.session_state["current_session_id"] = sessions[0]["id"]
    st.session_state["pending_prompt"] = ""
    save_sessions(sessions)


def split_response_and_references(content: str) -> tuple[str, list[str]]:
    """
    把回答正文和"参考来源"拆开。

    RAG 最终返回的是一段完整文本，这里按约定格式拆分，
    方便前端把正文和引用来源分开展示。
    """
    if not content:
        return "", []

    match = re.search(r"\n参考来源：\s*\n(?P<refs>(?:- .+\n?)*)$", content.strip())
    if not match:
        return content.strip(), []

    body = content[: match.start()].strip()
    refs_block = match.group("refs")
    references = [line[2:].strip() for line in refs_block.splitlines() if line.startswith("- ")]
    return body, references


def render_references(references: list[str]):
    """把引用来源渲染成标签，并支持展开查看预览片段。"""
    if not references:
        return

    chips = "".join(f'<span class="ref-chip">{reference}</span>' for reference in references)
    st.markdown(
        f"""
        <div class="ref-wrap">
            <div class="ref-title">参考来源</div>
            <div>{chips}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for index, reference in enumerate(references, start=1):
        with st.expander(f"查看片段 {index}: {reference}", expanded=False):
            st.caption("命中的本地知识片段预览")
            st.write(load_reference_preview(reference))


def parse_reference_label(reference: str) -> tuple[str, int | None]:
    """从"文件名 / 文件名 + 页码"格式中解析出来源和页码。"""
    match = re.match(r"^(?P<source>.+?)(?: 第 (?P<page>\d+) 页)?$", reference.strip())
    if not match:
        return reference.strip(), None
    source = match.group("source").strip()
    page = match.group("page")
    return source, int(page) - 1 if page else None


@st.cache_data(show_spinner=False)
def load_reference_preview(reference: str) -> str:
    """
    读取引用来源的本地预览片段。

    txt 直接读取文件开头片段；
    pdf 优先按页码读取对应页内容。
    """
    source, page = parse_reference_label(reference)
    abs_path = get_abs_path(f"data/{source}")
    if not abs_path or not source:
        return "未能解析参考来源。"

    try:
        if source.lower().endswith(".txt"):
            with open(abs_path, "r", encoding="utf-8") as f:
                preview = clean_text(f.read())[:420]
                return preview or "该文本来源没有可展示的预览内容。"
        if source.lower().endswith(".pdf"):
            docs = pdf_loader(abs_path)
            if page is not None and 0 <= page < len(docs):
                return clean_text(docs[page].page_content)[:420] or "该页没有可展示内容。"
            if docs:
                return clean_text(docs[0].page_content)[:420] or "PDF 没有可展示内容。"
            return "PDF 没有可展示内容。"
    except FileNotFoundError:
        return f"本地未找到来源文件：{source}"
    except Exception as e:
        logger.warning(f"加载参考片段失败：{reference}, error={str(e)}")
        return f"无法读取该来源的片段预览：{source}"

    return f"当前仅支持预览 txt/pdf 来源，文件：{source}"


def render_message(message: dict):
    """统一渲染一条消息，自动处理正文和引用来源。"""
    body, references = split_response_and_references(message["content"])
    st.write(body or message["content"])
    render_references(references)


# ===== 知识库管理函数 =====
def get_data_path():
    """获取知识库数据目录的绝对路径。"""
    return get_abs_path(chroma_conf["data_path"])


def get_uploaded_files():
    """获取已上传的知识库文件列表。"""
    data_path = get_data_path()
    allowed_types = tuple(chroma_conf["allow_knowledge_file_type"])
    return listdir_with_allowed_type(data_path, allowed_types)


def save_uploaded_file(uploaded_file) -> tuple[bool, str]:
    """
    保存上传的文件到知识库目录。

    Args:
        uploaded_file: Streamlit UploadedFile 对象

    Returns:
        (success: bool, message: str)
    """
    try:
        data_path = get_data_path()
        os.makedirs(data_path, exist_ok=True)

        # 清理文件名，防止安全问题
        filename = os.path.basename(uploaded_file.name)
        # 只保留字母数字和中文字符，以及常见的文件扩展名
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)

        if not filename:
            return False, "文件名无效"

        file_path = os.path.join(data_path, filename)

        # 检查文件是否已存在
        if os.path.exists(file_path):
            # 计算 MD5 判断是否相同文件
            existing_md5 = get_file_md5_hex(file_path)
            new_content = uploaded_file.getvalue()
            new_md5 = hashlib.md5(new_content).hexdigest()
            if existing_md5 == new_md5:
                return False, f"文件 '{filename}' 已存在且内容相同"
            # 文件名相同但内容不同，添加时间戳后缀
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{name}_{timestamp}{ext}"
            file_path = os.path.join(data_path, filename)

        # 保存文件
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        logger.info(f"文件上传成功：{file_path}")
        return True, filename
    except Exception as e:
        logger.error(f"保存上传文件失败：{str(e)}", exc_info=True)
        return False, f"保存文件失败：{str(e)}"


def delete_knowledge_file(filename: str) -> tuple[bool, str]:
    """
    删除知识库中的文件。

    Args:
        filename: 文件名（相对于 data_path）

    Returns:
        (success: bool, message: str)
    """
    try:
        data_path = get_data_path()
        file_path = os.path.join(data_path, filename)

        # 安全检查：确保文件在数据目录内
        real_file_path = os.path.realpath(file_path)
        real_data_path = os.path.realpath(data_path)
        if not real_file_path.startswith(real_data_path):
            return False, "非法的文件路径"

        if not os.path.exists(file_path):
            return False, f"文件 '{filename}' 不存在"

        os.remove(file_path)
        logger.info(f"删除知识库文件：{file_path}")
        return True, f"已删除 '{filename}'"
    except Exception as e:
        logger.error(f"删除文件失败：{str(e)}", exc_info=True)
        return False, f"删除失败：{str(e)}"


def load_document_to_vector_store():
    """触发向量库重新加载文档。"""
    try:
        rag_service.vector_store.load_document(force_reload=True)
        rag_service._collection_ready_checked = True
        return True, "知识库更新完成"
    except Exception as e:
        logger.error(f"更新知识库失败：{str(e)}", exc_info=True)
        return False, f"知识库更新失败：{str(e)}"


# ===== 新的知识库管理服务（带分页）=====

try:
    from rag.knowledge_base_service import KnowledgeBaseService
    kb_manager = KnowledgeBaseService()
    KB_SERVICE_AVAILABLE = True
except Exception as e:
    logger.warning(f"知识库管理服务加载失败：{e}")
    kb_manager = None
    KB_SERVICE_AVAILABLE = False


def get_kb_file_list(page: int = 1, page_size: int = 10, keyword: str = "", file_type: str = "") -> dict:
    """获取分页的文件列表"""
    if not KB_SERVICE_AVAILABLE or kb_manager is None:
        return {"error": "知识库管理服务不可用"}
    result = kb_manager.list_files(page=page, page_size=page_size, keyword=keyword, file_type=file_type)
    return result.to_dict()


def get_kb_chunk_list(page: int = 1, page_size: int = 10, source: str = "", source_type: str = "", keyword: str = "") -> dict:
    """获取分页的切片列表"""
    if not KB_SERVICE_AVAILABLE or kb_manager is None:
        return {"error": "知识库管理服务不可用"}
    result = kb_manager.list_chunks(page=page, page_size=page_size, source=source, source_type=source_type, keyword=keyword)
    return result.to_dict()


def add_kb_text_content(content: str, title: str = "手动输入内容") -> tuple[bool, str]:
    """添加文本内容到知识库"""
    if not KB_SERVICE_AVAILABLE or kb_manager is None:
        return False, "知识库管理服务不可用"
    return kb_manager.add_text_content(content=content, title=title)


def delete_kb_file(filename: str) -> tuple[bool, str]:
    """删除整个文件"""
    if not KB_SERVICE_AVAILABLE or kb_manager is None:
        return False, "知识库管理服务不可用"
    return kb_manager.delete_file(filename)


def search_kb(query: str, page: int = 1, page_size: int = 10, k: int = 10) -> dict:
    """搜索知识库"""
    if not KB_SERVICE_AVAILABLE or kb_manager is None:
        return {"error": "知识库管理服务不可用"}
    result = kb_manager.search_chunks(query=query, page=page, page_size=page_size, k=k)
    return result.to_dict()


with st.sidebar:
    # 侧边栏负责会话管理，体验上接近常见大模型产品的历史会话区。
    st.markdown("## 会话管理")
    sidebar_action_cols = st.columns(2)
    if sidebar_action_cols[0].button("新建会话", use_container_width=True):
        create_new_chat()
        st.rerun()
    if sidebar_action_cols[1].button("删除当前", use_container_width=True):
        delete_current_chat()
        st.rerun()

    st.caption("历史会话")
    current_session = get_current_session()
    for session in st.session_state["sessions"]:
        label = session["title"] or "新对话"
        if st.button(
            label,
            key=f"session_{session['id']}",
            use_container_width=True,
            type="primary" if session["id"] == current_session["id"] else "secondary",
        ):
            switch_session(session["id"])
            st.rerun()

    st.markdown("---")
    st.markdown("## 📤 上传文档")

    # 文件上传区域
    uploaded_files = st.file_uploader(
        "选择要上传的文件（txt/pdf）",
        type=["txt", "pdf"],
        accept_multiple_files=True,
        key="knowledge_uploader",
        help="支持 txt 和 pdf 格式"
    )

    if uploaded_files:
        st.caption(f"已选择 {len(uploaded_files)} 个文件")
        if st.button("确认上传", key="confirm_upload", use_container_width=True):
            success_files = []
            failed_files = []
            for uploaded_file in uploaded_files:
                success, msg = save_uploaded_file(uploaded_file)
                if success:
                    success_files.append((uploaded_file.name, msg))
                else:
                    failed_files.append((uploaded_file.name, msg))

            if success_files:
                st.success(f"成功上传 {len(success_files)} 个文件")
                for orig_name, saved_name in success_files:
                    st.write(f"- {orig_name}")

                # 自动更新知识库
                with st.spinner("正在更新知识库..."):
                    ok, msg = load_document_to_vector_store()
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
                st.rerun()

            if failed_files:
                for name, msg in failed_files:
                    st.warning(f"{name}: {msg}")

    st.markdown("---")
    st.markdown("## 知识库管理")

    # 分页参数初始化
    if "kb_page" not in st.session_state:
        st.session_state["kb_page"] = 1
    if "kb_page_size" not in st.session_state:
        st.session_state["kb_page_size"] = 10
    if "kb_keyword" not in st.session_state:
        st.session_state["kb_keyword"] = ""
    if "kb_file_type" not in st.session_state:
        st.session_state["kb_file_type"] = ""

    # Tab 切换：文件列表 / 搜索 / 手动添加
    kb_tabs = st.tabs(["📁 文件列表", "🔍 搜索知识"])

    with kb_tabs[0]:
        # 过滤条件
        col_filter1, col_filter2 = st.columns(2)
        with col_filter1:
            keyword_input = st.text_input("关键字搜索", value=st.session_state["kb_keyword"], key="kb_keyword_input", placeholder="输入文件名关键字...")
        with col_filter2:
            file_type_opt = st.selectbox("文件类型", ["", "txt", "pdf"], key="kb_type_select", index=0 if st.session_state["kb_file_type"] == "" else (2 if st.session_state["kb_file_type"] == "pdf" else 1))

        # 分页设置
        col_pg1, col_pg2 = st.columns([1, 2])
        with col_pg1:
            page_size_options = [5, 10, 20, 50]
            selected_page_size = st.selectbox("每页数量", page_size_options, index=page_size_options.index(st.session_state["kb_page_size"]) if st.session_state["kb_page_size"] in page_size_options else 1, key="kb_pagesize_select")
        with col_pg2:
            st.caption(f"提示：共 {len(get_uploaded_files())} 个文件，当前第 {st.session_state['kb_page']} 页")

        # 更新 session state
        st.session_state["kb_keyword"] = keyword_input
        st.session_state["kb_file_type"] = file_type_opt
        st.session_state["kb_page_size"] = selected_page_size

        # 获取分页数据
        kb_data = get_kb_file_list(
            page=st.session_state["kb_page"],
            page_size=selected_page_size,
            keyword=keyword_input,
            file_type=file_type_opt
        )

        if "error" in kb_data:
            st.error(kb_data["error"])
        elif kb_data["total"] == 0:
            st.info("暂无文档")
        else:
            # 显示分页文件列表
            files = kb_data["items"]
            for f in files:
                filename = f.get("filename", "")
                chunk_count = f.get("chunk_count", 0)
                created_at = f.get("created_at", "")[:10] if f.get("created_at") else ""

                col_name, col_info, col_del = st.columns([4, 2, 1])
                with col_name:
                    st.write(f"📄 **{filename}**")
                with col_info:
                    st.caption(f"")
                with col_del:
                    if st.button("🗑️", key=f"del_kb_{filename}", help=f"删除 {filename}"):
                        success, msg = delete_kb_file(filename)
                        if success:
                            st.success(msg)
                            st.session_state["kb_page"] = 1  # 重置到第一页
                            st.rerun()
                        else:
                            st.error(msg)

            # 分页导航
            total_pages = kb_data["total_pages"]
            current_page = kb_data["page"]

            col_nav1, col_nav2, col_nav3 = st.columns([1, 2, 1])
            with col_nav1:
                if current_page > 1:
                    if st.button("上一页", key="kb_prev_page", use_container_width=True):
                        st.session_state["kb_page"] = max(1, current_page - 1)
                        st.rerun()
                else:
                    st.caption("上一页")

            with col_nav2:
                st.caption(f"第 {current_page} / {total_pages} 页")

            with col_nav3:
                if current_page < total_pages:
                    if st.button("下一页", key="kb_next_page", use_container_width=True):
                        st.session_state["kb_page"] = min(total_pages, current_page + 1)
                        st.rerun()
                else:
                    st.caption("下一页")

    with kb_tabs[1]:
        # 搜索区域
        search_query = st.text_input("请输入搜索关键词", key="kb_search_input", placeholder="例如：回充失败、漏水处理...")
        search_k_param = st.slider("返回最多结果数", 1, 50, 10, key="kb_search_k")
        search_pg_size = st.selectbox("搜索结果每页数量", [5, 10, 20], index=1, key="kb_search_pagesize")

        if st.button("🔍 开始搜索", key="kb_search_btn"):
            if search_query.strip():
                with st.spinner("正在搜索..."):
                    results = search_kb(query=search_query, page=1, page_size=search_pg_size, k=search_k_param)
                    if "error" in results:
                        st.error(results["error"])
                    elif results["total"] == 0:
                        st.info("未找到相关结果")
                    else:
                        st.success(f"找到 {results['total']} 条相关结果")
                        for i, chunk in enumerate(results["items"], 1):
                            with st.expander(f"结果 {i}: "):
                                st.write(chunk.get("content", ""))
                                st.caption(f"来源：{chunk.get('source', '')} | 类型：{chunk.get('source_type', '')}")
            else:
                st.warning("请输入搜索关键词")


    # 知识库操作按钮
    st.markdown("---")
    col_op1, col_op2 = st.columns(2)
    with col_op1:
        if st.button("🔄 重新加载", use_container_width=True):
            with st.spinner("正在重新加载..."):
                ok, msg = load_document_to_vector_store()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            st.rerun()
    with col_op2:
        if st.button("🧹 清空重建", use_container_width=True):
            if st.checkbox("确认清空？", key="confirm_reset", help="此操作不可恢复"):
                with st.spinner("正在重建知识库..."):
                    try:
                        rag_service.vector_store.reset_store(clear_md5=True)
                        rag_service.vector_store.load_document(force_reload=True)
                        rag_service._collection_ready_checked = True
                        st.success("知识库已重建")
                        st.rerun()
                    except Exception as e:
                        logger.error(f"重建知识库失败：{str(e)}", exc_info=True)
                        st.error(f"重建失败：{str(e)}")

st.markdown(
    """
    <div class="hero-wrap">
        <h1 class="hero-title">扫地机器人智能客服</h1>
        <p class="hero-sub">快速解答选购、故障排查、维护保养与使用技巧，支持多轮对话。</p>
        <span class="stat">知识库问答</span>
        <span class="stat">故障诊断建议</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.write("")
action_cols = st.columns([1, 1, 4])
if action_cols[0].button("清空会话"):
    # 清空的是"当前会话"的消息，不影响其他历史会话。
    persist_current_messages([])
    st.session_state["pending_prompt"] = ""
    st.rerun()

current_session = get_current_session()
current_messages = current_session.get("messages", [])

# 页面展示的始终是"当前会话"的消息。
if not current_messages:
    st.info("可以先试试上面的快捷问题，也可以直接在下方输入你的需求。")

for message in current_messages:
    avatar = "🧑" if message["role"] == "user" else "🤖"
    with st.chat_message(message["role"], avatar=avatar):
        render_message(message)

input_prompt = st.chat_input("请输入你的问题，例如：拖地有水痕怎么处理？")
prompt = input_prompt or st.session_state.get("pending_prompt", "")

# 初始化流式输出状态
if "stream_response" not in st.session_state:
    st.session_state["stream_response"] = ""
if "stream_finished" not in st.session_state:
    st.session_state["stream_finished"] = False
if "stream_error" not in st.session_state:
    st.session_state["stream_error"] = None
if "is_streaming" not in st.session_state:
    st.session_state["is_streaming"] = False

# 重置流式状态（新对话时）
if prompt and not st.session_state["stream_finished"]:
    st.session_state["stream_response"] = ""
    st.session_state["stream_finished"] = False
    st.session_state["stream_error"] = None
    st.session_state["is_streaming"] = True

if prompt:
    st.session_state["pending_prompt"] = ""
    with st.chat_message("user", avatar="🧑"):
        st.write(prompt)
    # 先写入用户消息，再调用 Agent，这样异常时也能保留用户输入。
    current_messages = current_messages + [{"role": "user", "content": prompt}]
    persist_current_messages(current_messages)

    try:
        import time

        # 创建空容器用于实时更新显示
        response_placeholder = st.empty()

        # 启动流式输出生成器并累积完整响应
        stream_generator = st.session_state["agent"].execute_stream(current_messages)

        full_response = []
        displayed_text = ""

        # 接收后端流式输出并逐字显示
        for chunk in stream_generator:
            full_response.append(chunk)
            displayed_text += chunk

            # 处理参考来源（只在最后处理）
            if "参考来源" not in displayed_text:
                body, _ = split_response_and_references(displayed_text)
                display_text = body or displayed_text
            else:
                display_text = displayed_text

            # 存储到 session state 用于前端显示
            st.session_state["stream_response"] = display_text

            # 使用 markdown 实时更新显示
            response_placeholder.markdown(f"**🤖** {display_text}")
            time.sleep(0.02)

        # 最终清理和显示完整响应
        response_text = displayed_text.strip()
        if not response_text:
            response_text = "暂时没有生成有效回答，请重试。"
        elif "执行出错" in response_text:
            response_text = "处理您的问题时遇到了技术问题，请重试或换个方式提问。"

        # 标记流式输出完成
        st.session_state["stream_response"] = response_text
        st.session_state["stream_finished"] = True
        st.session_state["is_streaming"] = False

        # 显示参考来源
        body, references = split_response_and_references(response_text)
        render_references(references)

    except Exception as e:
        logger.error(f"对话处理失败：{str(e)}", exc_info=True)
        st.session_state["stream_error"] = "服务暂时不可用，请稍后重试。"
        st.session_state["stream_finished"] = True
        st.session_state["is_streaming"] = False

    # 显示流式输出内容
    with st.chat_message("assistant", avatar="🤖"):
        if st.session_state["stream_error"]:
            st.write(st.session_state["stream_error"])
        else:
            st.write(st.session_state["stream_response"])

    # 如果流式输出已完成，保存最终结果
    if st.session_state["stream_finished"] and not st.session_state["stream_error"]:
        current_messages = current_messages + [{"role": "assistant", "content": st.session_state["stream_response"]}]
        persist_current_messages(current_messages)

    st.rerun()
else:
    # 如果是流式输出过程中，显示当前进度
    if st.session_state.get("is_streaming", False):
        with st.chat_message("assistant", avatar="🤖"):
            st.write(st.session_state.get("stream_response", ""))
        st.rerun()
