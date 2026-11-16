# Star Math · 基于 RAG 的数学知识库智能问答系统

面向数学领域（数学分析 / 线性代数 / 概率统计）的检索增强问答系统。支持知识库管理、带来源引用的流式问答、检索策略对比评测，以及**图片或文档上传后自动识别题目并逐题解答**。

![智能问答](ui_shots/02_after_chat.png)

## 功能

- **知识库管理**：上传 / 解析 / 切分 / 向量化 / 重建，支持 md、txt、docx、pdf，批量导入目录
- **检索与问答**：向量语义 + BM25 关键词双路召回，RRF 融合排序，SSE 流式输出，回答附带来源片段与引用编号
- **检索评测**：内置 100 题标注测试集，支持 RAG 与基线（直调大模型）对照、单题与批量评测
- **上传解题**：上传图片或文档 → OCR 识别题面（RapidOCR 离线，可切换视觉大模型）→ LLM 结构化切题 → 逐题检索并生成思路、分步解答、答案与知识点 → 归档习题册并导出 Markdown
- **鉴权与会话**：scrypt 口令哈希（旧 sha256 账号登录时自动升级）、HMAC 签名令牌 + token_epoch 版本控制（改密后旧令牌立即失效）、登录失败限流、防用户名枚举的密码找回（一次性重置码 + 冷却）、邀请码注册；会话历史按用户隔离
- **自动化测试**：19 项 pytest 覆盖鉴权一致性、检索链路与向量库

![测试与评估](ui_shots/04_after_eval.png)

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python、FastAPI、Uvicorn、SQLite、自建向量库（numpy 余弦相似度）、纯内存 BM25、scrypt 口令哈希 + HMAC 签名令牌 |
| 检索 | 向量召回 + BM25 + RRF（k=60）融合；jieba 分词，缺失时回退字符 n-gram |
| 生成 | OpenAI 兼容 Chat Completions，SSE 流式；未配置密钥时降级为本地抽取模式 |
| OCR | RapidOCR（本地离线），可选 pix2text 或视觉大模型 |
| 前端 | 原生 JavaScript + HTML/CSS，无框架依赖 |

## 核心实现

### 混合检索与 RRF 融合

单用向量检索在专有术语上容易漂移，单用关键词检索又无法处理同义改写，因此两路并行召回后做融合：

- **向量路**：字符 n-gram / 远程 Embedding 生成向量，余弦相似度排序
- **关键词路**：BM25（k1=1.5，b=0.75），纯内存索引，零外部服务依赖
- **融合**：Reciprocal Rank Fusion，`score = Σ 1/(k + rank_i)`，k=60。之所以按排名而非分数加权，是因为余弦相似度与 BM25 分数的量纲不可比
- **业务加权**：概念卡文本短、BM25 底分天然偏低，会被长文本的反例集挤出候选，因此对概念卡单独加权

### 检索效果评测

自建 100 题标注测试集（概念 / 简答 / 综合 / 对比四类，标注期望来源章节与难度），Top-K=5 下的检索指标：

| 检索模式 | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 向量（vector） | 97.0% | 98.0% | 99.0% | 0.9775 |
| 关键词（keyword） | 96.0% | 98.0% | 100% | 0.9733 |
| **混合（hybrid）** | **98.0%** | **99.0%** | **100%** | **0.9858** |

分模块看，关键词检索在概率统计模块的 hit@1 仅 78.6%，混合后回升至 92.9%——双路互补在术语密度低、表述多样的模块上收益最明显。评测脚本见 `backend/scripts/eval_retrieval.py`，原始结果见 `backend/storage/eval_retrieval.json`。

**但这组数字不能直接用来比较检索算法优劣。** 按 [docs/项目审计报告.md](docs/项目审计报告.md) 的消融实验：hit@1 的 98% 中约 72 个百分点来自标题匹配加权规则，纯 RRF 融合仅贡献 23 个百分点；且 96/100 道题的题干直接包含期望文档的概念名，构成词面泄漏（lexical leakage），三路检索的指标因此失去区分度。结论是这组数据可用于验证链路连通、但不适合论证混合检索优于单路——重构测试集（改写题干、避免概念名直出）是后续要做的第一件事。

## 快速开始

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt
```

模型密钥支持两种放法（优先级：进程环境变量 > 用户级密钥文件 > 项目内 `.env`）：

- **推荐**：把密钥写进项目目录之外的文件，打包/分发源码时不会连带流出。
  Windows 为 `%APPDATA%\StarMath\.env`，Linux/macOS 为 `~/.config/star-math/.env`；也可用环境变量 `STAR_MATH_ENV_FILE` 指向任意 `.env`。该文件只需三行：

  ```
  LLM_API_KEY=sk-...
  VISION_LLM_API_KEY=sk-...
  LLM_API_BASE=https://api.deepseek.com/v1
  LLM_MODEL=deepseek-chat
  ```

- **快速试跑**：直接复制 `backend/env.example` 为 `backend/.env` 填写也行。密钥留空则运行在本地抽取模式，仍可完整演示问答与检索。

需要提交或外发源码时，用打包脚本生成制品，它会自动排除 `.env*`、数据库、向量库与开发残留：

```bash
cd backend
python scripts/make_delivery.py              # 安全默认：不含密钥与本机数据
python scripts/make_delivery.py --with-data  # 连 app.db 与向量库一起打包，方便对方直接演示
python scripts/make_delivery.py --list       # 只打印将打包/排除的清单
```

导入知识库并启动：

```bash
python scripts/ingest_kb.py --path "你的知识库目录"
python run.py
```

也可双击 `start.bat`（Windows）一键拉起后端并打开浏览器。访问 <http://127.0.0.1:8000>。

管理员账号由 `backend/.env` 中的 `DEFAULT_ADMIN_USER` / `DEFAULT_ADMIN_PASSWORD` 决定，首次启动创建用户时写入数据库；若用户已存在，则沿用数据库中的口令，修改配置不会自动覆盖已有账号。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录，返回签名令牌 |
| POST | `/api/auth/register` | 注册（需邀请码） |
| POST | `/api/auth/forgot-password` | 申请重置（返回统一提示，不暴露账号是否存在） |
| POST | `/api/auth/reset-password` | 凭一次性重置码改密 |
| POST | `/api/auth/change-password` | 已登录状态改密（旧令牌立即失效） |
| GET | `/api/me` | 当前用户 |
| POST | `/api/docs/upload` | 上传文档并入库 |
| GET | `/api/docs/{id}` | 文档详情（含分块预览） |
| POST | `/api/kb/ingest-folder` | 批量导入目录 |
| POST | `/api/chat` | RAG 问答（SSE 流式） |
| POST | `/api/search/debug` | 检索调试（可切换 hybrid/vector/keyword） |
| POST | `/api/eval/run` | 单题评测 |
| GET | `/api/eval/metrics` | 检索指标汇总 |
| GET | `/api/test-questions` | 测试问题集 |
| POST | `/api/exercises/upload` | 上传文档/图片，识别习题 |
| POST | `/api/exercises/{id}/solve` | 逐题 RAG 解题（SSE 流式） |
| GET | `/api/exercises/export` | 导出习题册 Markdown |
| POST | `/api/admin/users/{id}/reset-code` | 管理员签发重置码 |
| GET | `/api/admin/password-requests` | 查看待处理的找回申请 |

## 测试

```bash
cd backend
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q
```

当前 19 项用例全部通过（约 2 秒），覆盖：

- **鉴权一致性**：`main` / `exercise` / `practice` 三处鉴权行为相同；旧 token_epoch 的令牌在两处受限接口上都返回 401；未知用户的令牌被拒；旧 sha256 账号登录后自动升级为 scrypt；scrypt 往返校验；连续失败达阈值返回 429
- **检索**：混合检索融合结果与来源字段
- **向量库**：写入、检索与索引自愈

## 目录结构

```
backend/
  app/                # FastAPI 应用
    main.py           # 路由与接口
    vector_store.py   # 向量库 + BM25 + RRF 融合
    rag.py            # 检索与生成链路
    parsers.py        # 文档解析（含父子分块）
    exercise.py       # 上传解题与习题册
    practice.py       # 练习与判分
    llm.py            # LLM 客户端与流式解析
    security.py       # 口令哈希与令牌
    quota.py          # LLM 每日配额
    deps.py           # 统一鉴权依赖
    errors.py         # 统一错误码与用户可见提示
    logging_setup.py  # 日志初始化
  tests/              # pytest 用例
  scripts/            # 入库、评测、出题、安全打包脚本
  data/               # 100 题测试集
frontend/             # 原生 JS 前端
ui_shots/             # 界面截图
docs/                 # 设计说明与项目审计报告
```

## 已知限制

- 测试集为单人标注，且存在词面泄漏（见上文评测一节），指标不能用于比较检索算法；未做双人一致性校验
- 向量库为本地文件实现，规模限于千级片段；数据量增大后需要改用专用向量数据库
- 单机单用户演示定位，未做并发压测与细粒度权限分级
- 审计报告列出的 P0/P1 项已修复其中大部分，剩余项与处理状态见 [docs/项目审计报告.md](docs/项目审计报告.md)
- LLM 令牌有时效，过期后问答接口返回 401，需重新签发
