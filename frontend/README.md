# Context Lab 前端

Vue 3 + Vite + TypeScript 的多模态学习知识库工作台。前端只访问 FastAPI，不直接连接 Milvus、Redis、视觉模型或问答模型，也不保存任何 API 密钥。

## 页面

- `/home`：资料数量、最近资料和后台任务概览。
- `/ingest`：拖拽上传，实时展示接收、解析、视觉理解、分块、向量入库和完成状态。
- `/chat`：选择资料和回答模型，先展示混合检索状态，再生成带来源的回答。
- `/chunks`：查看知识块长度分布、原始顺序、页码、OCR 置信度和 metadata。

## 开发启动

后端和 Docker 服务启动后，在新 PowerShell 窗口执行：

```powershell
cd E:\github项目\rag\frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:5173`。

## 模型切换安全边界

前端从 `/api/v1/models` 读取模型 ID、名称和配置状态。提问时只提交 `model` 字段，后端根据白名单选择 API 地址和密钥。模型密钥只配置在项目根目录 `.env`：

```dotenv
LLM_LUNA_API_KEY=
LLM_DEEPSEEK_FLASH_API_KEY=
```

填写新密钥并重启 FastAPI 后，前端会自动显示对应模型为可用。视觉模型配置保持独立，不通过前端切换。

## 构建检查

```powershell
npm run build
```

## 目录结构

```text
frontend/
├── src/
│   ├── api/              # FastAPI 请求和接口类型
│   ├── components/       # 侧栏、状态轨道、模型选择、来源列表
│   ├── router/           # Vue Router 页面路由
│   ├── stores/           # Pinia 资料、任务和服务状态
│   ├── styles/           # 主题令牌和全局工作台样式
│   └── views/            # 总览、入库、问答、切片检查
├── package.json
├── vite.config.ts
└── tsconfig*.json
```
