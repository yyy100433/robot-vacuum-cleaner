# 扫地机器人智能客服

基于 LangChain 和 RAG 技术的智能客服系统，专为扫地机器人产品提供选购咨询、故障排查、维护保养与使用技巧的多轮对话服务。

## 功能特性

- **知识库问答**: 支持上传 PDF/TXT 格式的产品文档，构建向量数据库进行精准检索
- **多轮对话**: 基于大语言模型的上下文理解能力，支持自然流畅的多轮交流
- **参考来源**: 回答附带知识来源引用，可点击查看原始片段
- **会话管理**: 支持历史会话的创建、切换、删除，自动保存对话记录
- **知识库管理**: 支持文件的上传、删除、搜索和分页管理
- **RAG 检索增强**: 智能检索相关知识点，提高回答准确性和可信度

## 技术栈

- **前端 UI**: Streamlit
- **LLM 框架**: LangChain
- **向量数据库**: ChromaDB
- **大模型**: 阿里云通义千问 (DashScope)
- **嵌入模型**: text-embedding-v4

## 项目结构

```
langchain-agent/
├── app.py                 # 主应用程序入口
├── config/                # 配置文件目录
│   ├── agent.yaml        # Agent 配置
│   ├── chroma.yaml       # 向量库配置
│   ├── prompt.yaml       # 提示词路径配置
│   ├── rag.yaml          # 模型配置
│   └── skills.yaml       # 技能配置
├── prompts/               # 提示词文件
│   ├── main_prompt.txt
│   ├── rag_summarize.txt
│   └── report_prompt.txt
├── data/                  # 数据目录
│   ├── external/         # 外部数据文件
│   └── [knowledge docs]  # 知识库文档
├── utils/                 # 工具模块
│   ├── bootstrap.py      # 启动验证
│   ├── config_handler.py # 配置加载器
│   ├── file_handler.py   # 文件处理
│   ├── logger_handler.py # 日志处理器
│   └── path_tool.py      # 路径工具
├── agent/                 # Agent 相关
│   ├── react_agent.py    # React 智能体
│   ├── planning.py       # 规划模块
│   └── tools/            # 工具定义
├── rag/                   # RAG 相关
│   ├── vector_store.py   # 向量存储
│   ├── rag_service.py    # RAG 服务
│   └── knowledge_base_service.py  # 知识库服务
└── model/                 # 模型工厂
    └── factory.py        # 模型工厂
```

## 安装步骤

### 1. 克隆项目

```bash
git clone <repository-url>
cd langchain-agent
```

### 2. 创建虚拟环境并安装依赖

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量

复制环境变量示例文件并配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件，添加您的 DashScope API Key：

```
DASHSCOPE_API_KEY=your_api_key_here
```

### 4. 准备知识库文档

将产品相关的 PDF/TXT 文档放入 `data/` 目录。

### 5. 启动应用

```bash
streamlit run app.py
```

浏览器会自动打开 http://localhost:8501

## 配置文件说明

### config/rag.yaml

```yaml
chat_model_name: qwen3-max-2025-09-23  # 聊天模型名称
embedding_model_name: text-embedding-v4  # 嵌入模型名称
```

### config/chroma.yaml

```yaml
collection_name: agent                    # 集合名称
persist_directory: storage/chroma_db      # 持久化目录
k: 4                                      # 检索返回数量
chunk_size: 240                           # 默认切块大小
pdf_chunk_size: 420                       # PDF 切块大小
txt_chunk_size: 220                       # TXT 切块大小
allow_knowledge_file_type: ["txt", "pdf"] # 允许的文件类型
```

## 使用说明

1. **上传文档**: 在侧边栏选择 PDF/TXT 文件上传到知识库
2. **开始对话**: 在输入框输入问题，如"拖地有水痕怎么处理？"
3. **查看引用**: 回答下方显示参考来源，点击可查看原文片段
4. **管理会话**: 使用侧边栏新建/删除/切换会话
5. **搜索知识**: 在知识库管理标签页搜索特定内容

## 注意事项

- 需要有效的 DashScope API Key 才能正常使用
- 首次上传文档时需要等待知识库构建完成
- 建议上传清晰可读的 PDF 或结构化的 TXT 文档
- 创建一个.env填入自己的apikey,cozeapitoken和cozebotid
- 创建一个.env.example填入自己的redis配置和记忆系统配置

## 许可证

本项目仅供学习和研究使用。
