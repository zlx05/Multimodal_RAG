# 学习文档：多模态解析与 OCR

> 目标读者：项目作者本人（要面试、要能讲清楚）。看完这份文档，你应该能：
> 1. 说清楚为什么需要多模态解析、DocumentBlock 是什么。
> 2. 复述 PDF / 图片 / Markdown 各自的解析流程。
> 3. 说清楚 OCR 是什么、PaddleOCR GPU 怎么配、有哪些坑。
> 4. 回答面试官关于"怎么解析、OCR 出错怎么办"的追问。

---

## 1. 这一模块解决什么问题

### 1.1 业务痛点

学生的复习资料不只是 `.txt`。真实资料长这样：

| 资料 | 形态 | 难点 |
| --- | --- | --- |
| 教材 PDF | 有文字层 | 页内还有公式图、插图 |
| 扫描版教材 | 纯图片 | 没有任何文字层 |
| 错题截图 | 图片 | 整页都是图片 |
| 手写笔记 | 图片 | OCR 难度高于印刷体 |
| Markdown / TXT | 纯文本 | 要保留标题层级 |

一个 RAG 系统如果只能处理纯文本，那 **扫描版 PDF、错题截图、手写笔记就直接丢了**——这些恰恰是学生复习里最有价值的部分（错题、手写总结）。

### 1.2 解决方案的一句话

> 把不同格式的资料统一解析成一个中间结构 **DocumentBlock**，解析完之后的检索、分块、问答都不再关心原始文件是 PDF 还是图片。

这个"统一中间结构"是核心设计。类比：不管输入是中文还是英文，翻译成同一种"内部语言"后再处理。

---

## 2. 核心概念

### 2.1 DocumentBlock：统一中间结构

代码在 [blocks.py](../backend/app/rag/blocks.py)。它长这样：

```python
@dataclass
class DocumentBlock:
    document_id: str            # 属于哪个文档
    source_type: str            # pdf | markdown | image | text
    content_type: str           # text | table | image_ocr | formula | heading
    text: str                   # 文本内容（OCR 结果也放这里）
    page_number: int | None     # PDF 页码（溯源用）
    image_path: str | None      # 原图路径（溯源用，前端展示原图）
    bbox: tuple | None          # 图片中的文本区域坐标（溯源用）
    heading_path: list[str]     # 标题层级路径，如 ["第一章", "1.1 随机事件"]
    confidence: float | None    # OCR 置信度（判断是否可信）
    metadata: dict              # 扩展字段
```

**为什么要它？** 假设没有它，PDF 解析器返回 `list[str]`，图片解析器返回 `list[dict]`，Markdown 返回 `list[tuple]`……上层代码就要写一堆 `if source_type == ...` 的分支。有了 DocumentBlock，所有解析器都返回同一个类型，上层写一次逻辑就全部适用。这就是"统一契约"。

### 2.2 OCR：把图片里的字变成文本

OCR（Optical Character Recognition，光学字符识别）的任务是**从图片像素里认出文字**。流程大致是：

```
图片
  -> 检测（Detection）：找出文字在哪个位置（文本框坐标）
  -> 识别（Recognition）：把每个文本框里的图像识别成文字
  -> 输出：文本 + 每个文本框坐标 + 置信度
```

项目用的 **PaddleOCR** 是百度开源的 OCR 框架，中文识别效果在开源方案里属于第一梯队，支持 GPU 加速。本项目把"检测"和"识别"合在一起用（`PaddleOCR().ocr(image)` 直接返回文字）。

### 2.3 PDF 的两类页面

这是 PDF 解析最容易讲清楚也最容易翻车的点：

- **文本页**：PDF 里存了文字层（复制粘贴能选中文字），直接用 PyPDF 提取。
- **扫描页**：PDF 就是一页大图片（扫描件），提取不出文字，必须渲染成图片再 OCR。

判断方法：`page.extract_text()` 返回的字符数 >= 20 就是文本页，否则按扫描页处理。

**为什么这个判断重要？** 因为很多"教材 PDF"是混排的：前面几页是目录（文本），正文里有扫描图表。必须逐页判断，不能一刀切。

### 2.4 为什么还要 OCR 文本页里的图片

即使文本页有文字层，页面里还嵌着**公式图、插图、表格截图**。这些图片里的文字 PyPDF 也提取不到。所以要"文本 + 图片"双通道：

```
文本页
  -> PyPDF 提取段落文字   （main text）
  -> 提取页内嵌图片 -> OCR （公式、插图）
```

两个通道都产出 DocumentBlock，都带页码，检索时都能命中。

---

## 3. 代码怎么读

### 3.1 入口：解析器工厂

[parsers/__init__.py](../backend/app/rag/parsers/__init__.py) 的 `create_parser()` 根据扩展名选解析器：

```python
SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf", ".md": "markdown", ".txt": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ...
}

def create_parser(document_id, path, **kwargs) -> BaseParser:
    parser_type = get_parser_type(path)
    if parser_type == "pdf":      return PdfParser(document_id, **kwargs)
    if parser_type == "markdown": return MarkdownParser(document_id)
    ...
```

上层调用方只做一件事：`parser = create_parser(id, path)` 然后 `blocks = parser.parse(path)`。它不需要知道内部是 PDF 还是图片。

### 3.2 Markdown 解析器（最简单，先看这个）

[markdown_parser.py](../backend/app/rag/parsers/markdown_parser.py) 按 `#` 标题层级分段：

```python
def _parse_text(self, text):
    heading_stack = []          # 维护当前标题路径
    for line in text.split("\n"):
        if line.startswith("#"):
            # 更新 heading_stack（遇到同级/上级标题就弹出）
            yield self._block(title, content_type="heading", heading_path=list(heading_stack))
        else:
            current_text.append(line)   # 正文行累积
    # 段落累积完毕，作为一个 block 产出
    yield self._block(content, heading_path=list(heading_stack))
```

关键点：**heading_path 是累计的**。比如 `# 第一章` 下 `## 1.1 随机事件`，那么 1.1 下面的正文 block 的 `heading_path = ["第一章", "1.1 随机事件"]`。这个路径以后会拼进检索文本，提升"按知识点检索"的准确率。

### 3.3 图片解析器

[image_parser.py](../backend/app/rag/parsers/image_parser.py)：

```python
def parse(self, path):
    stored_path = str(path)
    if self.copy_original and self.original_dir:   # 把原图复制到管理目录
        shutil.copy2(path, target)
        stored_path = str(target)
    result = self.ocr_engine(path)                 # OCR 识别
    return [self._block(result.text, content_type="image_ocr",
                        image_path=stored_path, confidence=result.confidence)]
```

要点：
- **原图路径一定要保留**。OCR 文本用于检索，原图用于前端"点开看原图复核"。
- 即使 OCR 结果为空，也返回一个空 block，让上层知道"这个图处理过了"（而不是被漏掉）。

### 3.4 PDF 解析器（最复杂）

[pdf_parser.py](../backend/app/rag/parsers/pdf_parser.py)：

```python
def parse(self, path):
    reader = PdfReader(str(path))
    for page_idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if len(text) >= TEXT_PAGE_MIN_CHARS:     # 文本页
            blocks += self._parse_text_page(page_idx, text)
            blocks += self._parse_page_images(page_idx, page)   # 页内嵌图也 OCR
        else:                                     # 扫描页
            blocks.append(self._parse_scanned_page(page_idx))   # 整页 OCR
```

- `_parse_text_page`：把页面文本按空行分段成多个 block，每个带页码。
- `_parse_page_images`：提取页面里嵌的图片（`page.images`），逐个 OCR。
- `_parse_scanned_page`：用 pypdfium2 把整页渲染成图片（2x 放大提高识别率），再 OCR。

### 3.5 OCR 引擎（可插拔）

[ocr/paddle_engine.py](../backend/app/rag/ocr/paddle_engine.py) 实现了一个 `BaseOcrEngine` 接口：

```python
class BaseOcrEngine:
    def __call__(self, image_path) -> OcrResult: ...   # 必须实现

@dataclass
class OcrResult:
    text: str
    boxes: list[list[float]]    # 文本框坐标
    confidences: list[float]
    confidence: float           # 整体置信度
```

**为什么抽象？** 面试官会问"如果 PaddleOCR 效果不好怎么办"。回答是：因为封装了接口，可以随时换 EasyOCR、RapidOCR 或云 OCR，上层解析器不用改。这就是"面向接口编程"。

`PaddleOcrEngine.__init__` 的 `use_gpu` 参数：
- 显式传 `True`/`False` 强制指定；
- 不传时自动检测 GPU（`paddle.device.is_compiled_with_cuda()`）。

---

## 4. PaddleOCR GPU 安装（踩坑记录，必看）

这是本项目最大的环境坑，直接写在这里。

### 4.1 为什么不能直接 `pip install paddlepaddle-gpu`

PyPI 上的 `paddlepaddle-gpu` 最高只有 **2.6.2**，而 PaddleOCR 3.x 需要 **paddlepaddle >= 3.0**。所以 GPU 版必须从 **Paddle 官方源**装：

```bash
# CUDA 12.6 环境（本项目）
python -m pip install paddlepaddle-gpu==3.0.0 \
    -i https://www.paddlepaddle.org.cn/packages/stable/cu126/

# 装完 GPU 版再装 PaddleOCR
pip install paddleocr==3.7.0
```

### 4.2 版本怎么选

| 你的 GPU 驱动 CUDA 版本 | 装哪个源 |
| --- | --- |
| 12.6 | `.../stable/cu126/` |
| 11.8 | `.../stable/cu118/` |

**注意**：这里的 CUDA 版本和 PyTorch 无关。你的 PyTorch 可能是 cu121，但 PaddlePaddle 有自己独立的 CUDA 绑定，两者可以共存，互不影响。

### 4.3 验证 GPU 是否生效

```python
import paddle
print(paddle.__version__)                      # 3.0.0
print(paddle.device.is_compiled_with_cuda())   # True 才说明是 GPU 版
print(paddle.device.is_available())            # True 说明检测到 GPU
```

### 4.4 实测踩坑

1. **首次运行会下载模型**：PaddleOCR 检测/识别模型约十几 MB，首次调用自动下载，需要网络。模型会缓存到本地，之后不用再下。
2. **Windows 控制台编码**：OCR 结果含生僻字/公式下标（如 `ₙ`），`print` 可能报 `UnicodeEncodeError`。运行脚本时加 `PYTHONIOENCODING=utf-8`。
3. **扫描页放大识别**：分辨率太低的小字识别差，渲染 PDF 页时用 `scale=2.0` 放大，识别率明显提升。
4. **手写 vs 印刷**：手写笔记 OCR 置信度显著低于印刷体。这正是要保存 `confidence` 字段的原因——低置信度内容在回答时要谨慎引用。

---

## 5. 面试问答

### Q1：为什么要把不同格式统一成 DocumentBlock？

**参考回答**：因为检索、分块、问答这些下游逻辑不应该关心文件类型。如果每个解析器返回各自的格式，下游就要写大量 `if source_type` 分支。统一成一个结构后，解析和检索解耦——以后加新格式（比如 Word）只写一个新解析器，下游零改动。

### Q2：PDF 怎么处理"有文字层"和"扫描页"两种情况？

**参考回答**：逐页判断。用 PyPDF 提取文本，字符数达到阈值就当文本页，直接提取段落；低于阈值说明是扫描页，用 pypdfium2 渲染成图片交给 OCR。而且文本页里也可能嵌着公式图，会再提取图片做 OCR，两个通道的结果都带页码。

### Q3：OCR 出错怎么办？

**参考回答**（这是 interview-handbook.md 第 12 节 + 实际实现）：
- 保存 OCR 置信度，低置信度文本在回答时降低权重或明确标注。
- 原图和 OCR 文本同时保留，前端可以"点开原图复核"。
- 公式和表格不要只依赖 OCR，保留图片作为最终来源。
- 手写笔记 OCR 置信度天然低，展示原图作为复核入口。

### Q4：为什么选 PaddleOCR？

**参考回答**：中文场景识别效果好，开源免费，支持 GPU 加速。项目是中文学生资料，手写和印刷混排，PaddleOCR 的中文模型最合适。而且封装了接口，效果不好可以换 RapidOCR / 云 OCR。

### Q5：OCR 的置信度怎么用？（进阶追问）

**参考回答**：置信度是识别可信度的数值（0~1）。用途：
1. **检索阶段**：低置信度的 block 可以降权，避免把 OCR 错的文本当正确答案召回。
2. **生成阶段**：把置信度传给 LLM，让它在资料不确定时说明，而不是自信地编造。
3. **展示阶段**：前端用置信度决定是否强调"这是 OCR 结果，建议看原图"。

### Q6：如果给这个模块加一个新格式（比如 Word）？

**参考回答**：继承 `BaseParser`，在 `SUPPORTED_EXTENSIONS` 加扩展名，实现 `parse()` 返回 DocumentBlock 即可。解析器和下游完全解耦。

---

## 6. 你该记住的"一句话总结"

> 多模态解析就是把 PDF、图片、Markdown 等异构资料，**通过不同解析器统一产出 DocumentBlock**。文本资料用 PyPDF/解析器直接提取，图片和扫描页用 PaddleOCR 识别，所有 block 都保留来源信息（页码、原图路径、标题路径、置信度），为后面的混合检索和溯源打基础。

面试问"你怎么处理学生手写笔记" → 答"用 OCR 识别成文本 + 保留原图 + 低置信度处理"。问"怎么统一多格式" → 答"DocumentBlock 统一中间结构"。

---

## 7. 相关文件

| 文件 | 作用 |
| --- | --- |
| [blocks.py](../backend/app/rag/blocks.py) | DocumentBlock 数据结构 |
| [parsers/__init__.py](../backend/app/rag/parsers/__init__.py) | 解析器工厂（按扩展名选择） |
| [parsers/pdf_parser.py](../backend/app/rag/parsers/pdf_parser.py) | PDF 文本页 + 扫描页 |
| [parsers/image_parser.py](../backend/app/rag/parsers/image_parser.py) | 图片 OCR |
| [parsers/markdown_parser.py](../backend/app/rag/parsers/markdown_parser.py) | Markdown 标题层级 |
| [ocr/paddle_engine.py](../backend/app/rag/ocr/paddle_engine.py) | PaddleOCR 引擎 |
| [ocr/base.py](../backend/app/rag/ocr/base.py) | OCR 抽象接口 |
| [scripts/test_parsers.py](../scripts/test_parsers.py) | 解析器端到端测试 |
| [scripts/test_ocr.py](../scripts/test_ocr.py) | OCR 引擎测试 |
