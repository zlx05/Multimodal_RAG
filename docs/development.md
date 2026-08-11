# 开发说明

## 环境准备

~~~powershell
conda activate rag11
python -m pip install -r backend/requirements.txt
~~~

### PaddleOCR GPU 安装（多模态阶段）

PaddleOCR 3.x 需要 paddlepaddle >= 3.0。PyPI 上的 paddlepaddle-gpu 最高只有 2.6.x，
GPU 版必须从 Paddle 官方源按 CUDA 版本安装：

~~~powershell
# CUDA 12.6（本项目 RTX 4060 驱动对应版本）
python -m pip install paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
python -m pip install paddleocr==3.7.0
~~~

装完验证 GPU 是否生效：

~~~powershell
python -c "import paddle; print(paddle.device.is_compiled_with_cuda())"  # 应为 True
~~~

> 注意：paddlepaddle 的 CUDA 版本与 PyTorch 无关，两者独立共存。模型首次运行自动下载到 `C:/Users/<user>/.paddlex`。

其余多模态依赖：`pypdf`、`pypdfium2`（PDF 页渲染）、`rank-bm25`（关键词检索）、`redis`（异步任务）。

.env 只保存在本地，不提交到 Git。Embedding 模型路径应通过 EMBEDDING_MODEL 配置，不要写入 Python 源码。

中文 Embedding 使用 BGE-small-zh-v1.5。下载模型：

~~~powershell
conda activate rag11
python scripts/download_embedding_model.py
~~~

验证模型维度：

~~~powershell
python -c "from backend.app.rag.model_config import EMBEDDING_MODEL; from sentence_transformers import SentenceTransformer; print(EMBEDDING_MODEL); print(SentenceTransformer(EMBEDDING_MODEL).get_embedding_dimension())"
~~~

正常结果应为 512。多模态阶段依赖暂存于 backend/requirements-multimodal.txt，其中 PaddleOCR 还需要根据 Windows、CPU/GPU 环境单独确认 PaddlePaddle 安装方式。

## 配置第三方模型

当前项目统一使用 OpenAI 兼容的第三方接口：视觉模型负责入库阶段的图像理解，文本模型负责检索后的最终问答。两者可以使用同一个 API 密钥，密钥只写入项目根目录 `.env`，不要放进 Vue 前端：

~~~dotenv
LLM_API_KEY=${VISION_LLM_API_KEY}
LLM_BASE_URL=https://sudobug.top/v1
LLM_MODEL=gpt-5.6-luna
VISION_LLM_API_KEY=填写你的密钥
VISION_LLM_BASE_URL=https://sudobug.top/v1
VISION_LLM_MODEL=gpt-5.6-terra
~~~

代码会在 `LLM_API_KEY` 为空、为 `ollama` 或仍使用变量占位符时自动复用 `VISION_LLM_API_KEY`。`.env` 已被 `.gitignore` 忽略，不会提交到 GitHub。修改模型配置后必须重启 FastAPI 和 Worker。

### 多模态 RAG 的模型分工

最终问答模型不需要直接读取原始图片，因为入库阶段已经完成图像理解、OCR、公式识别和结构化。处理链路是：

~~~text
PDF / 图片 / Markdown
        |
PyPDF / PaddleOCR / 视觉模型提取文本、页码、图片来源和 OCR 元数据
        |
统一文档块 -> BGE 中文 Embedding -> Milvus
        |
BM25 + 向量检索 -> 文本上下文、出处、页码 -> 第三方文本模型生成答案
~~~

面试时应明确区分“多模态数据处理/检索”和“多模态生成模型”：本项目的多模态能力主要集中在解析与上下文构建阶段，最终回答使用文本上下文完成，便于替换模型供应商并控制显存成本。

### 配置视觉解析模型

视觉模型只在入库阶段使用，负责图片、手写笔记、PDF 页面、DOCX/PPTX 内嵌图片的补充理解；最终问答使用同一第三方接口的文本模型。将密钥写入项目根目录 `.env`，不要放进 Vue 前端：

~~~dotenv
VISION_LLM_API_KEY=填写你的密钥
VISION_LLM_BASE_URL=https://sudobug.top/v1
VISION_LLM_MODEL=gpt-5.6-terra
VISION_LLM_TIMEOUT=90
FORMULA_RECOGNITION_ENABLED=true
FORMULA_RECOGNITION_MODEL=PP-FormulaNet_plus-M
OCR_DEVICE=cpu
FORMULA_RECOGNITION_DEVICE=cpu
PADDLE_PDX_CACHE_HOME=models/paddlex
~~~

没有配置视觉密钥时，系统仍会运行 PaddleOCR 和文本解析；视觉模型调用失败也会回退到 OCR，不会丢弃整份资料。若 PaddleOCR 在本机 OneDNN/CUDA 环境失败，图片任务会记录警告并继续走视觉解析。当前上传接口支持 `.pdf`、`.md`、`.txt`、`.doc`、`.docx`、`.ppt`、`.pptx`、`.xlsx`、`.csv` 和常见图片格式。旧版 `.doc`/`.ppt` 会通过本机 Microsoft Office COM 自动转换为 `.docx`/`.pptx`，因此 Worker 运行机器必须安装 Office。PDF 解析优先用 `pdfplumber`（阅读顺序 + 表格/标题检测），失败逐页回退 PyPDF。

解析结果会保留原文件、图片路径、页码/幻灯片号、OCR 框、置信度、表格数据和视觉描述。Word/PPT 原生公式直接从 OMML 转换为 LaTeX；图片和扫描 PDF 优先使用视觉模型识别公式，只有视觉不可用时才接收经过重复模式和长度校验的 PaddleOCR `PP-FormulaNet_plus-M` 结果。模型缓存位于 `models/paddlex`。默认将 OCR/公式模型放在 CPU，避免占用显存。不能把普通 OCR 结果当作可靠公式。

## 启动依赖服务

~~~powershell
conda activate rag11
docker compose -f infra/docker-compose.yml up -d
~~~

首次准备环境时再安装依赖和下载 Embedding 模型；日常启动不需要重复执行：

~~~powershell
conda activate rag11
python -m pip install -r backend/requirements-multimodal.txt
python scripts/download_embedding_model.py
~~~

## 启动 FastAPI 与 Worker

API 和 Worker 需要分别在两个 PowerShell 窗口运行：

窗口一：

~~~powershell
cd E:\github项目\rag
conda activate rag11
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8504
~~~

API 首次启动会加载本地中文 Embedding 模型并连接 Milvus，通常需要 30～60 秒。看到 `Uvicorn running on http://127.0.0.1:8504` 后，再访问健康检查接口确认服务可用：

~~~powershell
Invoke-RestMethod http://127.0.0.1:8504/api/v1/health
~~~

窗口二：

~~~powershell
cd E:\github项目\rag
conda activate rag11
python -m backend.app.tasks.worker
~~~

不需要启动 Ollama。启动后访问 `http://127.0.0.1:8504/docs`，健康检查地址为 `http://127.0.0.1:8504/api/v1/health`。

## 开发约定

- API、业务服务、基础设施访问和数据模型分层，路由函数不直接编写 OCR、向量化或 Milvus 细节。
- 文件路径、模型路径、服务地址全部从配置读取。
- 长耗时任务使用 Redis，禁止在请求线程内同步执行整本 PDF OCR。
- 所有入库数据保存来源元数据和内容哈希。
- 新接口使用 /api/v1，旧 /api 接口只作为迁移兼容层。

## 验证清单

~~~powershell
python -m compileall backend
python -m pytest backend/tests
~~~

当前自动化测试覆盖视觉客户端、图片 OCR + 视觉合并、DOCX/PPTX 文本与表格解析以及解析器工厂。Milvus、Redis、PaddleOCR GPU 的完整链路需要在本地依赖服务启动后单独验证。

运行示例（Windows 控制台中文编码需指定 UTF-8）：

~~~powershell
conda activate rag11
set PYTHONIOENCODING=utf-8
python -m pytest backend/tests -q
~~~
