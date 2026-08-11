# Backend tests

测试按模块拆分，优先覆盖：

- 文档解析和 DocumentBlock 元数据。
- 分块边界和内容哈希。
- BM25、向量检索和混合排序。
- Redis 任务状态转换。
- API 请求校验和来源字段。

测试目录当前只保留约定说明，后续随多模态解析、任务队列和混合检索实现补充自动化测试。

运行测试：

```powershell
conda activate rag11
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```
