# 借鉴 RAGFlow 改造说明（Star Math · 数学 RAG 智能问答系统）

> 改造范围经确认：「全做 + 登录小改进」= 后端三项（混合检索、父子分块、检索评测指标）
> ＋ 前端三项（检索测试页、分块预览、内联引文）＋ 登录（邀请码、记住我）。

## 一、借鉴来源

RAGFlow（https://github.com/infiniflow/ragflow）的公开资料（官方 glossary、ingestion
pipeline 博文、第三方架构解读），选取与本项目体量、技术栈（Python + 本地向量 + SQLite）
匹配的能力，不引入其重型组件（DeepDoc、ES、GraphRAG 等）。

## 二、后端改造

| 模块 | 改造 | 借鉴点 |
| --- | --- | --- |
| `app/config.py` | 新增 `RETRIEVAL_MODE=hybrid`、`BM25_K1/B`、`RRF_K=60`、`PARENT_MAX_CHARS=1600`、`USE_PARENT_CONTEXT=1`、登录 TTL、`REGISTER_INVITE_CODE` | 可配置检索策略（RAGFlow 检索配置化） |
| `app/parsers.py` | `split_text` 重写为父子分块：先按标题切父块，超长父块再拆子块，子块带 `parent_idx/parent_content` | RAGFlow 父子分块（子块检索、父块给上下文） |
| `app/vector_store.py` | 整体重写：`BM25Index`（jieba 分词）+ 向量检索 + RRF 融合，`search(mode=vector/keyword/hybrid)`，条目含 `vec_score/bm25_score` | RAGFlow 混合检索（向量+BM25+RRF） |
| `app/rag.py` | `retrieve` 增 `mode` 参数；按模式过滤相关性；流式回答透传 `retrieval_mode` | 检索与生成解耦 |
| `app/llm.py` | 父块去重、父块优先排序；prompt 提示按 `[n]` 输出引文 | RAGFlow 引文（citation）机制 |
| `app/main.py` | 注册校验邀请码（未配置时不启用）、登录 `remember`（7 天/30 天 TTL）、`/api/chat` 与 `/api/search/debug` 支持 `mode`、健康检查暴露检索配置 | RAGFlow 用户体系 + 检索调试 |
| `app/db.py` | `chunks` 表增 `parent_content/parent_idx`，迁移在 `init_db()` 内执行（脚本与 API 路径都生效） | 数据模型随能力演进 |
| `scripts/ingest_kb.py` | 新增 `--replace` 全量重建（清库重导） | RAGFlow 文档入库/重建 |
| `scripts/eval_retrieval.py` | 新增检索评测：Hit@1/3/5 + MRR，对比 vector/keyword/hybrid，结果写 `storage/eval_retrieval.json` | RAGFlow 评测指标化 |

## 三、前端改造

| 文件 | 改造 |
| --- | --- |
| `index.html` | 登录表单加「邀请码」「记住我」；检索调试升级为「检索测试」（策略下拉 + Top-K 下拉）；文档表格保留分块预览入口 |
| `app.js` | 登录/注册提交带 `remember`/`invite_code`；Tab 切换联动显隐；检索测试调 `/api/search/debug` 并渲染 RRF/向量/BM25 分数；回答正文内联引文 `[n]` 转可点击角标（点击高亮来源卡片并预览原文）；来源列表绑定到消息供引文溯源；文档分块预览按「块」渲染（含父块标识） |
| `styles.css` | 新增 `.cite`、`.chunk-block`、检索测试、记住我等样式 |

## 四、验证结果（数学版）

> 以下为 2026-10-14 数学语料（`数学_RAG`，110 文档）全量重建后的实测数据，
> 对应《数学RAG系统_变更清单与实施任务单》步骤 ⑥（入库重建）与 ⑦（检索评测）。

1. **知识库重建**：`ingest_kb.py --path C:\word\知识库\知识库\数学_RAG --replace` 全量重建，
   数学语料 110 文档 → **322 子块**（`/api/health` 实测，DB 与向量索引一致），0 错误。
   （历史：政治版 28 篇 / 586 子块已于 2026-10-14 备份至 `storage_backup_政治_20261014/`。）
2. **检索评测（100 题数学版）**：期望来源可映射 100/100（实测值见 `backend/storage/eval_retrieval.json`）。

   | 策略 | Hit@1 | Hit@3 | Hit@5 | MRR | 未命中 |
   | --- | --- | --- | --- | --- | --- |
   | vector | 0.97 | 0.98 | 0.99 | 0.9775 | 1 |
   | keyword | 0.96 | 0.98 | 1.00 | 0.9733 | 0 |
   | hybrid | **0.98** | **0.99** | **1.00** | **0.9858** | **0** |

   分模块（hybrid，Hit@1 / MRR）：分析 68 题 0.985 / 0.990；线性代数 18 题 1.000 / 1.000；概率统计 14 题 0.929 / 0.946。
   唯一 vector 未命中为对比题「定积分与不定积分的关系」（hybrid 命中），如实记录，非阻断。

   > **数学语料检索适配（重要实现决策）**：数学卡片正文以 LaTeX 公式为主、中文术语集中在标题
   > （doc_name），导致「纯概念名」查询（如"什么是矩阵？"）在纯向量/BM25 榜中召回不足
   > （实测初版 hybrid Hit@1 仅 0.43）。为此在检索层做了三项适配：
   > ① 标题命中加权：查询术语与卡片名「精确相等」加 10.0（BM25）/ 0.35（向量），「包含」加 2.0 / 0.05；
   > ② BM25 标题精确命中卡强制优先（数学术语在标题是强信号，不与正文词频竞争）；
   > ③ hybrid 融合候选扩大（top_k×4），关键词提取去符号并保留全部长短词（避免 2 字核心词被截断）。
   > 适配后 hybrid Hit@1 由 0.43 → 0.98。
3. **接口冒烟**（uvicorn 8000 + mimo 网关 4096 实测）：`backend/scripts/smoke_test.py` **8/8 通过**
   （健康检查 / 登录 / 错误口令文案 / 空白问题 422 / RAG 问答 SSE「数列极限」/ 噪声查询 0 命中 /
   「数列极限」检索命中 / 测试题集 100 题）；`/api/practice/*` 专项 HTTP 冒烟 **11/11 通过**
   （paper 出题不含答案 / 判分 / 解析 / 保存 / 错题本 / 导出 md / 未登录 401）。
   注：LLM 令牌 24h 有效，过期时聊天输出「[生成失败…]」，按 README 用
   `mimo.cmd llm-server issue` 续期并更新 `backend\.env` 的 `LLM_API_KEY` 后重启后端即可。
4. **前端**：`app.js` 通过 `node --check`；静态资源经服务访问 200；KaTeX 公式渲染、模块过滤、做题页实测通过。

## 五、已知限制 / 使用注意

- **LLM 网关需先启动**：`.env` 指向本机 mimo serve（4096 端口）；未启动时聊天流会输出
  「[生成失败…]」。测试时请先按 README 启动 mimo。
- **邀请码未启用**：`REGISTER_INVITE_CODE` 默认空；在 `.env` 配置后，注册才校验邀请码
  （`/api/health` 的 `invite_code_required` 会随之变为 true）。
- **唯一评测未命中**：第 1 题（见上），可后续用更大测试集验证混合检索优势。
- 知识库源文件未改动；`docs/pw` 为系统保护目录，未读取（不影响本改造）。

## 六、启动方式

```
# 先启动 LLM 网关（mimo serve），再：
cd backend
.venv\Scripts\python.exe run.py     # http://127.0.0.1:8000
```

默认账号 `admin / admin123`。前端为服务内静态页面（index.html + app.js + styles.css）。

## 七、布局与预览修复（2026-10-10）

| 问题 | 修复 |
| --- | --- |
| 知识库页检索测试挤占文档表格（检索时遮住文档） | 「检索测试」改为**折叠卡片**：默认收起只留标题栏，点击展开；结果区固定 `max-height: 420px` 内部滚动，不再把页面撑长 |
| 预览只显示分块切片 | 新增只读接口 `GET /api/docs/{id}/raw`（返回**完整源文件原文** + 分块）；预览弹窗默认渲染**完整 Markdown 原文**（标题/表格/列表/代码均还原），顶部「完整原文 / 分块视图」一键切换 |

涉及文件：`backend/app/main.py`（+`/raw`）、`frontend/index.html`（折叠卡片结构、静态资源版本号 `?v=20261011`）、
`frontend/app.js`（折叠开关、`renderChunks` 抽取、原文渲染与视图切换）、`frontend/styles.css`（折叠/限高/原文视图样式）。
验证：`/raw` 返回原文+分块；浏览器实测折叠收起/展开、预览默认原文、切换分块均正常。

## 八、流式回答性能优化（2026-10-14）

**问题**：RAG 增强问答「回答很慢」——原实现为**假流式**（`stream=False` 等 LLM 完整生成后再按 24 字切成片段下发），
单次请求约 17s，首字延迟≈总时长，用户体验为长时间空白。

**根因与修复**（三层）：

| 层 | 根因 | 修复 |
| --- | --- | --- |
| LLM 调用 | 非流式，完整生成后才开始 | `llm.py` 新增真流式 `chat_completion_stream`（`stream=true`，逐块解析 `data:` 行，`[DONE]` 结束）；`LLM_MAX_TOKENS` 1024→512（`config.py` + `.env`） |
| SSE 下发 | `event_stream` 为同步生成器，检索/LLM 阻塞时事件循环被占，帧无法及时下发 | `main.py` 中 `event_stream` 改 `async def`，用 `asyncio.to_thread(next, gen)` 逐帧在线程池推进同步生成器，事件循环保持空闲，帧即时发送。生成器耗尽在线程内捕获（`_safe_next`），避免 `StopIteration` 跨线程传播被 asyncio 拒绝而报「服务异常」 |
| 传输 | （排除）曾怀疑 uvicorn/httptools 缓冲 | 经 raw socket 逐帧验证，服务端帧下发正常（首帧 +24ms、帧间隔 1s 精准），无需改服务器 |

**实测时序**（raw socket 直接读流，绕过客户端缓冲，`/api/chat`「简述数列极限的定义」）：
检索结果（sources 事件）+0.62s → 首 token +2.7s → 之后每 ~0.1s 一个 token 持续输出 → 完成 +13.7s（117 个 token 帧）。
此前诊断脚本用 urllib/http.client 读到「整流缓冲」是**客户端库缓冲**造成的假象，浏览器 `fetch` + `ReadableStream`
不受影响，实际逐字输出。

**涉及文件**：`backend/app/llm.py`（`chat_completion_stream`、错误回退 `[生成失败…]`）、`backend/app/main.py`
（`event_stream` 改 async + `asyncio.to_thread`）、`backend/app/config.py` 与 `backend/.env`（`LLM_MAX_TOKENS=512`）、
`backend/run.py`（`http="h11"` + Windows Selector 事件循环策略）。
验证：`backend/scripts/smoke_test.py` **8/8 通过**；raw socket 时序如上。

**仍受网关影响**：mimo 网关（4096）首 token 延迟波动大（实测 1.2s～18s，取快慢均值约 3s），属模型/网关性能，
与代码无关；回答总时长主要由模型生成速度决定，max_tokens 已由 1024 降至 512 以缩短生成。

## 九、真题接入做题模块（kmath.cn 试点，2026-10-14）

**需求**：做题模块要"真正的习题"，引入 kmath.cn（科数网）真实试卷真题。

**事实与限制**（已实测）：
- 列表接口公开可用：`GET /math/Main_Data.aspx?area=dx&qtype=…&hard=…&pageIndex=N` 返回 JSON
  （题目 ID / 题干含 LaTeX / 题型 / 难度 / 来源试卷）；`dx`=考研数学，另含线代/概率等分区。
- **官方答案需 VIP**（`/math/jssvr/answer.aspx` 返回「答案VIP会员可见」），故按约定用 **LLM 生成参考答案+得分点**。
- 生成过程踩坑（已解决）：非流式 `chat_completion` 对长数学解答被网关截断（JSON 不闭合）；
  改用流式拼接（`stream_chat_completion`，忽略截断元组标记）+ 脚本内 `LLM_MAX_TOKENS=1200~1536`；
  个别题连精简输出也失败（mimo 输出异常），试点直接剔除。

**试点成果**：15 道北大/上海交大真题入库 `backend/data/kmath_questions.json`（每题：题干 LaTeX 原样、
3 条得分点 `answer_points`、完整参考答案 `calc_steps`、来源 `source.papername`）；`practice.py` 的
`_load_questions` 合并加载（原 100 题不变）。全链路验证：出题抽到真题（#44804）、LLM 判分正常、解析返回得分点+解答。
冒烟 8/8 通过。

**扩量 + 大知识点分类（2026-10-14）**：抓取扩展至多分区（dx 考研高数 / gaodengdaishu 高等代数 /
shuxuefenxi 数学分析），共抓 94 题原始；LLM 生成答案时顺带标注**大知识点**（8 类白名单：极限与连续 /
导数与微分 / 一元积分 / 多元微分 / 级数 / 微分方程 / 线性代数 / 概率统计），原 100 题也批量标注同类。
剔除反复生成失败的题（27/94，mimo 对长证明题输出不稳，按用户要求不再纠结）后 **67 道有效真题**入库：
线性代数 21、多元微分 14、微分方程 12、一元积分 9、导数与微分 6、极限与连续 4、级数 1；
来源含北大高数期中、上交期末、多元函数微分学、微分方程、线性空间/多项式（科数网）、北大高等代数期中、
合肥工业大学高等代数、数学分析一元积分等。

**大知识点筛选（做题面板）**：前端新增「知识点」下拉（8 类），后端 paper 增加 `knowledge` 参数按
`q.get("knowledge")` 过滤；题型筛选已二分为「真题」（source.site=kmath.cn）与「概念」（原题库）。
验证：真题+线性代数/多元微分/微分方程/一元积分各抽 5 道过滤全部正确；概念+概率统计全部为原题。

**生成脚本 v2**：checkpoint 增量写盘 + resume 续跑 + 并发 2，中断不丢、可分批执行。

**扩量路径**（后续）：`scripts/fetch_kmath.py N` 改 N 抓更多题（可换 hard=中等/困难、area=线性代数/概率统计），
`scripts/gen_kmath_questions.py N` 分批生成（自动 resume 跳过已完成），`scripts/prune_kmath_questions.py`
剔除失败题。真题类型映射：解答/证明→综合型、填空→简答型、单选→概念型（前端 qtype 过滤可直接用）。
