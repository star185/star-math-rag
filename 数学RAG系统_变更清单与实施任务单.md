# Star Math（数学 RAG 系统）・变更清单与实施任务单

> **状态**
>
> ：用户已确认
>
> **允许修改代码**
>
> 。本文档是把《数学知识库应用到毕业设计 RAG 项目_完整方案》转成
>
> **可执行变更单**
>
> —— 逐文件、逐位置列出 “要更改的东西”，实施时按 §5 顺序执行、每步验证。
> 目标项目：
>
> `C:\word\Graduation Project1`
>
> （Star Math，原政治理论 RAG；FastAPI + 原生前端）
> 源知识库：
>
> `C:\word\知识库\知识库\数学`
>
> （Obsidian Vault + AGENT_SPEC 存入知识 skill）
> 语料目标目录：
>
> `C:\word\知识库\知识库\数学_RAG\`
>
> （新建）
> 核对日期：2026-10-14（行号基于当日文件状态）



***

## 0. 变更总览



| 编号    | 文件 / 目录                                                              | 动作                                  | 对应方案章节          | 优先级 |
| ----- | -------------------------------------------------------------------- | ----------------------------------- | --------------- | --- |
| D1    | `backend/storage/`                                                   | 备份（不修改）                             | § 七・阶段 0        | P0  |
| D2    | `C:\word\知识库\知识库\数学_RAG\`                                            | **新建**约 110 个 .md 语料                | § 五             | P0  |
| D3    | `backend/data/test_questions.json/.csv/.md`                          | **整体替换**为数学 100 题（扩字段）              | § 六 / § 十二・12.2 | P0  |
| C1    | `backend/app/config.py`                                              | 修改 2 处                              | §3.2·A          | P0  |
| C2    | `backend/app/llm.py`                                                 | 修改系统提示词 2 处（+F8/F9/M2 分支）           | §3.2·A / §11.3  | P1  |
| C3    | `backend/scripts/gen_test_questions.py`                              | 修改（政治→数学映射）                         | §3.2・A / § 六    | P1  |
| C4    | `frontend/index.html`                                                | 修改文案 5 处（+F1 引入 KaTeX + 做题页入口）      | §3.2·B / §11.1  | P1  |
| C5    | `frontend/app.js`                                                    | 修改文案 / 统计卡（+F1 渲染函数 +F4 下拉 + 做题页逻辑） | §3.2·B          | P1  |
| C6    | `frontend/styles.css`                                                | 追加样式（公式 / 做题 / 图谱等）                 | §11 / §12       | P1  |
| C7    | `README.md`、`改造说明_借鉴RAGFlow.md`                                      | 文案替换                                | §3.2·C          | P2  |
| F4    | `backend/app/main.py`                                                | 透传 `part` 参数（检索过滤）                  | §11.2           | P1  |
| F5    | `backend/data/symbol_alias.json`                                     | **新建**符号别名表 + `rag.py` 归一化          | §11.2           | P2  |
| F1    | `frontend/`（index/app.js/styles）                                     | KaTeX 公式渲染                          | §11.1           | P1  |
| F2    | `frontend/` 知识图谱页                                                    | **新增**页面（复用双链网络 html）               | §11.1           | P2  |
| F3    | `frontend/` 公式速查面板                                                   | **新增**组件（复用 `/api/docs/{id}/raw`）   | §11.1           | P2  |
| F7    | `backend/app/rag.py` + `llm.py`                                      | 反例联动                                | §11.2           | P1  |
| F11   | `backend/scripts/eval_retrieval.py`                                  | 分模块评测汇总                             | §11.4           | P1  |
| M1–M6 | `backend/app/practice.py`（**新建**）+ `db.py` + `main.py` + `frontend/` | 做题体系                                | § 十二            | P1  |



***

## 1. 数据层变更（非代码，先行）

### 1.1 D1 备份



```
复制 C:\word\Graduation Project1\backend\storage\  →  C:\word\Graduation Project1\backend\storage_backup_政治_20261014\
```

验证：备份目录存在、`app.db` 可读。

### 1.2 D2 导出数学语料 `C:\word\知识库\知识库\数学_RAG\`

从 vault 复制 5 类知识性文件夹为**扁平 md**，命名规则（对齐政治语料数字前缀风格）：



| 目标文件前缀             | 来源          | 数量 | 说明                     |
| ------------------ | ----------- | -- | ---------------------- |
| `00_目录.md`         | 手动生成        | 1  | 模块清单（分析 / 线性代数 / 概率统计） |
| `01_地图·极限与连续.md` 等 | `01-地图/`    | 5  | 5 张地图卡                 |
| `10_概念·{名}.md`     | `10-概念/`    | 41 | 每卡一文件，保留原 frontmatter  |
| `20_定理·{名}.md`     | `20-定理/`    | 9  | 定理卡                    |
| `30_反例·{名}.md`     | `30-例子与反例/` | 54 | 49 知识点 + 5 模块总览        |
| `50_公式速查·{名}.md`   | `05-公式速查/`  | 5  | 速查卡                    |

**必须做的两处内容加工**（其余逐字复制）：



1. **frontmatter 补&#x20;**`part`**&#x20;字段**（F4 模块过滤的数据基础）：

* 分析类卡 → `part: 分析`；线性代数类 → `part: 线性代数`；概率统计类 → `part: 概率统计`（地图卡按模块归属，公式速查卡按所属模块归属）

1. **文件名加模块前缀**已由上表保证（`10_概念·` 等），`gen_test_questions.py` 的 `00_` 排除规则与 `rag.py` 的 `00_` 过滤天然生效。

**排除**（不进入语料）：`00-开始这里/`、`40-问题与想法/`、`50-习题/`、`90-文献/`、`99-模板/`。

**保留**：`$...$` LaTeX 与 `[[双链]]` 原样（入库时 `parsers.strip_markdown_noise` 自动处理双链）。

验证：文件数 ≈ 110；抽查 3 卡 frontmatter 完整含 `part`；无 99 - 模板 / 问题记录类文件。

### 1.3 D3 数学测试题集（替换 `backend/data/test_questions.*`）

字段结构（沿用现有 + 做题体系扩展）：



```
{
  "type": "概念型",                 // 概念型/简答型/综合型/对比型
  "course": "分析",                 // 分析/线性代数/概率统计
  "module": "分析",
  "difficulty": "L1",               // L1 定义 / L2 性质 / L3 综合
  "mode": "choice",                 // choice/judge/short/calc
  "question": "什么是数列极限？",
  "options": ["…", "…", "…", "…"], // mode=choice 必填
  "answer_index": 1,
  "calc_steps": ["…", "…"],         // mode=calc 必填（分步参考答案）
  "answer_points": ["…"],
  "expected_source": {"file": "10_概念·数列极限.md", "section": "定义", "pages": "", "book": ""},
  "keywords": ["数列极限", "ε-N 语言"]
}
```

构成（100 题）：概念型 34（含选择 / 判断 14 + 简答 20）/ 简答型 33 / 综合型 30 / 对比型 3；计算题 ≥ 10 分布三模块。

15 道样例见主方案 §6.3；完整 100 题由 C3 改造后的脚本生成（或手工续写，格式如上）。

验证：`file` 字段全部能在 `数学_RAG/` 中找到；`node`/`python -c json.load` 校验合法；`/api/test-questions` 返回新集。

### 1.4 F5 符号别名表（`backend/data/symbol_alias.json`，新建）



```
{
  "别名": ["归一化关键词"],
  "ε-N 语言": ["数列极限", "epsilon-N"],
  "f'": ["导数", "微分"],
  "Σ": ["级数", "求和"],
  "P(A|B)": ["条件概率"],
  "lim": ["极限"],
  "∫": ["积分", "定积分", "不定积分", "反常积分"]
}
```

`rag.py` 检索前：query 命中别名 → 把归一化词并入检索词与 BM25 打分（仅扩展、不替换原词）。



***

## 2. 领域替换代码变更（政治 → 数学）

### C1 `backend/app/config.py`



| 位置     | 现状                                                                                                 | 改为                                                |
| ------ | -------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| L38–39 | 注释 `# 知识库源目录（清洗后政治理论笔记）`；`KB_SOURCE_DIR = Path(os.getenv("KB_SOURCE_DIR", r"C:\word\知识库\知识库\政治"))` | 注释改 “清洗后数学知识库语料”；默认值改 `r"C:\word\知识库\知识库\数学_RAG"` |
| L97    | `APP_NAME = "政治理论 RAG 智能问答系统"`                                                                     | `APP_NAME = "Star Math 智能问答系统"`                      |

### C2 `backend/app/llm.py`



| 位置                          | 现状                   | 改为                 |
| --------------------------- | -------------------- | ------------------ |
| L149（baseline 分支 system 首行） | `"你是政治理论领域的严谨问答助手。"` | `"你是数学领域的严谨问答助手。"` |
| L158（RAG 分支 system 首行）      | 同上                   | 同上（两处都改）           |

同时为 F8/F9/M2 预留分支（见 §3、§4，实施时在 `build_messages` 增 `mode` 分支）。

### C3 `backend/scripts/gen_test_questions.py`



| 位置       | 现状                                                        | 改为                                                                                                                                       |
| -------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| L9       | `KB = Path(r"C:\word\知识库\知识库\政治")`                        | `KB = Path(r"C:\word\知识库\知识库\数学_RAG")`                                                                                                   |
| L92–101  | `_course_of()`：文件名数字 `<10 → 毛概`，否则 `习概`                   | 模块映射：按文件名前缀 `01_地图·/10_概念·/20_定理·/30_反例·` 归 “分析”，`04_地图·线性代数`/ 线代类卡归 “线性代数”，`05_地图·概率统计`/ 概率类卡归 “概率统计”；或直接读 frontmatter `part` 字段（语料已补齐） |
| L103–152 | 遍历章节生成 概念 / 简答 / 综合 题                                     | 保留生成逻辑；章节取卡片字段级标题（定义 / 直觉 / 关键点 / 易错 / 反例 / 核心公式）作 section；`expected_source.file` 相对 KB 路径自动正确                                           |
| L156–186 | 不足时用章节标题补充                                                | 保留，逻辑通用                                                                                                                                  |
| L194–247 | `HAND` 4 道政治手写题（两个结合 / 中国式现代化 / 不敢腐…）                     | 替换为 5 道数学手写高价值题（对比型为主，见主方案 §6.3 的 id=10 等）                                                                                               |
| L252–271 | `mao/xi` 课程配比逻辑                                           | 改为 `mods = {"分析":[], "线性代数":[], "概率统计":[]}` 三池均衡，总量 100                                                                                  |
| L277–284 | payload `name=政治理论…`、`domain=政治理论（毛概+习概）`、`source_kb=…政治` | `name=Star Math 测试问题集`、`domain=数学（分析 + 线性代数 + 概率统计）`、`source_kb=C:/word/知识库/知识库/数学_RAG`                                                     |
| L302–307 | md 版标题 `政治理论 RAG 测试问题集`、领域行                               | 同步数学版；表头 “课程” 列改 “模块”                                                                                                                    |

验证：`python scripts/gen_test_questions.py` 输出 100 题；`file` 映射率 100%；TYPES / 模块分布打印正常。

### C4 `frontend/index.html`（5 处文案 + 2 处增强）



| 位置           | 现状                                                | 改为                           |
| ------------ | ------------------------------------------------- | ---------------------------- |
| L6           | `<title>政治理论 RAG 智能问答</title>`                    | `<title>Star Math 智能问答</title>` |
| L16          | `<h1>政治理论智能问答</h1>`                               | `<h1>Star Math 智能问答</h1>`            |
| L41          | `<span>政治理论 RAG</span>`                           | `<span>Star Math</span>`        |
| L92          | `<div class="muted small">基于政治理论知识库的检索增强问答</div>` | 基于 Star Math 数学知识库的检索增强问答               |
| L115         | `<p>提问政治理论相关问题，系统会检索知识库并给出带来源的回答。</p>`            | 提问数学相关问题…                    |
| （增强）`<head>` | —                                                 | 引入 KaTeX CDN（F1）             |
| （增强）导航区      | —                                                 | 加 “做题” 导航项（M1–M6）            |

### C5 `frontend/app.js`



| 位置         | 现状                                         | 改为                                                                   |
| ---------- | ------------------------------------------ | -------------------------------------------------------------------- |
| L1         | 注释 `政治理论 RAG 智能问答 — 前端逻辑`                  | `Star Math — 前端逻辑`                                                 |
| L464       | `<p>提问政治理论相关问题…</p>`                       | 数学版                                                                  |
| L466–469   | 4 个政治建议问题（两个结合 / 三大法宝 / 全面从严治党 / 中国式现代化）   | 4 个数学建议问题（什么是数列极限？/ 简述收敛判别法的主要内容。/ 为什么说微积分基本定理很重要？/ 定积分与不定积分的关系是什么？） |
| L1143–1145 | 统计卡 `part === "毛概"`/`"习概"`，fallback `政治理论` | `part === "分析"`/`"线性代数"`/`"概率统计"` 三卡，fallback `数学`                   |

### C6 `frontend/styles.css`

追加：KaTeX 公式排版、做题页（题卡 / 选项 / 解析折叠 / 错题本）、图谱页、仪表盘等样式（随 F/M 实施逐项追加）。

### C7 文档



* `README.md`：L3 领域行、L63 导入路径、L83 功能对照（28 篇→语料篇数、103 题→100 题）、L120 课程分布行，全部改数学版；

* `改造说明_借鉴RAGFlow.md`：标题、验证数据（586 子块等）按数学版实测值重写；

* 两份政治体验报告 .docx：归档保留，不进入数学版（答辩材料另出）。



***

## 3. 功能扩展代码变更（F1–F12）



| 编号  | 功能           | 变更内容                                                                                                                        | 复用 / 新增                                          | 验收一句话                |
| --- | ------------ | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | -------------------- |
| F1  | 公式渲染         | `index.html` 引 KaTeX CDN；`app.js` 加 `renderMath(html)`（消息 / 解析 / 题面渲染前调用，`$...$`/`$$...$$` → KaTeX）；`styles.css` 公式样式       | 新增函数                                             | ε-N 定义以公式显示          |
| F2  | 知识图谱页        | `frontend/` 新页面：加载双链 JSON（后端新增 `GET /api/kb/graph` 输出 nodes/edges，或前端直接加载静态 `知识库双链网络.html` 数据），点节点跳 `/api/docs/{id}/raw` 预览 | 新接口或静态复用                                         | 数列极限→函数极限→连续性… 链路可展开 |
| F3  | 公式速查面板       | 知识库页折叠面板，调 `GET /api/docs` 按名过滤 `50_公式速查` + `GET /api/docs/{id}/raw` 渲染原文                                                   | 全复用现有接口                                          | 输入 “求导法则” 定位速查卡      |
| F4  | 模块过滤         | `main.py`：`ChatIn`/`SearchIn` 增可选 `part` 字段并透传 `rag.retrieve(part=…)`；`app.js`：问答 / 检索测试页加模块下拉（全库 / 分析 / 线性代数 / 概率统计）       | `rag.retrieve` 已支持 part                          | 下拉后命中仅来自该模块          |
| F5  | 符号别名         | 见 §1.4                                                                                                                      | 新数据文件 + `rag.py` 归一化                             | 问 “ε-N 语言” 召回数列极限卡   |
| F6  | 公式检索（可选，风险高） | `parsers.py` 子块增 `formulas` 字段（抽取 `$...$`，归一化去空白 / 统一变量）；`vector_store.py` 增公式索引与打分；`rag.py` 检索分支                           | 3 处结构性修改                                         | 输入 `∫ 1/x dx` 召回积分卡  |
| F7  | 反例联动         | `rag.py`/`llm.py`：命中定理卡时按命名关联（`30_反例·{定理名}`）补入 1 条反例块；组装上下文时标注「反例」                                                          | `build_messages` 扩展                              | “介值定理去掉连续会怎样” 引用反例集  |
| F8  | 双回答模式        | `llm.py` `build_messages` 增 \`style=strict                                                                                  | intuition` 分支（提示词分别要求定义式/直觉式）；`app.js\` 回答前加切换按钮 | `build_messages` 分支  |
| F9  | 解题步骤链        | `llm.py` RAG 提示词增步骤模板（条件→思路→结论，逐步引文）；`app.js` 步骤样式                                                                          | 提示词 + 样式                                         | 证根题输出分步解答            |
| F10 | 问题收集箱        | `main.py` 新 `POST /api/notes`（追加 问题 + 回答 + 来源 到 `backend/storage/我的问题记录.md`，格式对齐 vault 模板）；`app.js` “存为问题” 按钮               | 新接口 + 前端按钮                                       | 导出符合 vault 模板格式      |
| F11 | 分模块评测        | `eval_retrieval.py` 结果按 `course` 分组汇总（Hit@1/3/5+MRR 每模块一行）；写入 `eval_retrieval.json` 并输出                                     | 脚本扩展                                             | 报告指出弱模块              |
| F12 | 健康仪表盘        | `main.py` 新 `GET /api/kb/stats`（文档数 / 子块数 / 最近入库 / 模块分布 / 评测摘要）；`app.js` 首页顶栏组件                                             | 新接口 + 组件                                         | 入库后数字即时变化            |



***

## 4. 做题体系代码变更（M1–M6）

### 4.1 新增 `backend/app/practice.py`

模块职责与函数清单（纯逻辑，不碰现有 RAG 链路）：



```
load_questions() -> list[dict]                 # 读 data/test_questions.json
quiz(module, qtype, difficulty, n) -> list     # 随机抽题 + 缺口加权（读评测 json 弱模块优先）
grade(question, user_answer, mode) -> dict     # choice/judge: 本地对比 answer_index
                                               # self: 返回 answer_points 供自评
                                               # llm:  调 build_messages(mode="grade")
                                               # extract: 关键词命中率打分（离线兜底）
explain(question_id) -> dict                   # rag.retrieve(expected_source.file) + 反例联动 + 公式速查
save_record(...) / list_records(user_id, module, qtype) / mark_reviewed(id)
export_md(records) -> str                      # 渲染成 vault「50-习题/习题记录.md」模板格式
mastery(module) -> dict                        # 做题正确率 + 评测 Hit@1 合并为模块掌握度
```

### 4.2 `backend/app/db.py`：新表（追加到 SCHEMA，沿用现有迁移范式）



```
CREATE TABLE IF NOT EXISTS practice_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  question_id INTEGER NOT NULL,
  module TEXT DEFAULT '',
  qtype TEXT DEFAULT '',
  mode TEXT DEFAULT 'self',        -- choice / self / llm / extract
  user_answer TEXT DEFAULT '',
  correct INTEGER DEFAULT -1,      -- 1 对 / 0 错 / -1 未定（自评）
  self_grade TEXT DEFAULT '',      -- ok / partial / none
  score REAL DEFAULT 0,
  reviewed INTEGER DEFAULT 0,
  created_at REAL NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

（`init_db()` 内 `executescript(SCHEMA)` 已覆盖新表，无需 ALTER；`--replace` 重建与 API 路径都生效。）

### 4.3 `backend/app/main.py`：新增路由组 `/api/practice/*`



| 方法   | 路径                      | 说明                                   |
| ---- | ----------------------- | ------------------------------------ |
| POST | `/api/practice/quiz`    | 出题（module/qtype/difficulty/n）        |
| POST | `/api/practice/submit`  | 交卷判分（mode 三档），写 practice\_records    |
| GET  | `/api/practice/explain` | 解析（question\_id）                     |
| GET  | `/api/practice/records` | 做题记录 / 错题本（module/qtype/reviewed 过滤） |
| POST | `/api/practice/export`  | 导出今日练习 / 错题 md                       |
| GET  | `/api/practice/mastery` | 模块掌握度                                |

（鉴权沿用 `Depends(get_current_user)`；接口体量小，直接写 main.py 或从 practice.py 导入 handler。）

### 4.4 `backend/app/llm.py`：判分提示词分支

`build_messages(..., mode="grade")`：system = “你是数学判卷助手。按参考答案要点逐条比对用户答案，输出：评分（0-10）+ 评语 + 漏掉的要点编号。”—— 非流式调用（复用 `chat_completion`）。

### 4.5 前端做题页（`index.html` / `app.js` / `styles.css`）



* 导航新增 “做题” Tab；页面区块：出题参数（模块 / 题型 / 难度 / 题数默认 5）→ 题卡（公式渲染）→ 答题区（选项单选 / 文本框）→ 交卷（三判分模式按钮）→ 结果与 “查看解析”（折叠，来源可跳知识库）→ “存错题 / 导出今日练习” → “错题本”（筛选 / 重做 / 标已掌握）→ “掌握度” 图表（简易条形图即可）。



***

## 5. 执行顺序与验证（每步验证通过再进下一步）



| 步骤 | 内容                                          | 验证                                   | 回滚           |
| -- | ------------------------------------------- | ------------------------------------ | ------------ |
| ①  | D1 备份 storage/                              | 备份可读                                 | —            |
| ②  | D2 导出数学\_RAG 语料                             | 文件数 /part 字段 / 无噪声抽查                 | 删目录重导        |
| ③  | C1+C2（config/llm 文案）                        | `python -c "import app.config"`、启动无错 | git / 备份还原   |
| ④  | C4+C5+C6+C7（前端 + 文档文案）                      | `node --check app.js`；浏览器看无政治残留      | 还原文件         |
| ⑤  | D3+C3（测试题集 + 生成脚本）                          | 生成 100 题、映射率 100%                    | 还原数据文件       |
| ⑥  | 入库重建：`ingest_kb.py --path 数学_RAG --replace` | \[OK]×\~110；`/api/health` 规模正确       | storage 备份还原 |
| ⑦  | F4（模块过滤）+ F11（分模块评测）                        | 下拉过滤生效；评测输出模块表                       | 还原改动         |
| ⑧  | F1（公式渲染）+ F7（反例联动）                          | 公式显示；反例被引用                           | 还原改动         |
| ⑨  | M1–M5（做题核心闭环）                               | §12.6 验收 1–5 条                       | 还原改动         |
| ⑩  | F2/F3/F5/F8/F9/F10/F12（可选增强）                | 各自验收点                                | 还原改动         |
| ⑪  | F6（公式检索，最后评估）                               | 公式提问命中                               | 不做或回退        |

> 全流程守住三条：
>
> **每步先备份、改后必冒烟、可演示再进下一步**
>
> 。



***

## 6. 风险与注意事项（执行时对照）



1. `--replace`**&#x20;会清掉现有政治语料与评测记录**—— 步骤 ⑥ 前必须确认 D1 备份完成；

2. **frontmatter&#x20;**`part`**&#x20;字段**是 F4 / 模块统计的数据基础，D2 导出时务必补齐，否则统计卡与过滤失效；

3. `gen_test_questions.py`**&#x20;的&#x20;**`_course_of`**&#x20;与配比逻辑**是改动最集中的函数，改后必须重跑并核对 TYPES / 模块分布打印；

4. **LLM 判分主观**：默认自评模式，LLM 判分仅作增强（做题体系不阻塞）；

5. **公式检索（F6）风险最高**：如评测不佳，保留 `parsers` 的公式字段但关闭检索分支即可，不影响其他功能；

6. 每处文案替换后全项目 `grep -r "政治" `（排除 .docx/.venv/git）应仅剩两份历史体验报告；

7. 所有新增接口与页面完成后，跑 `smoke.bat`（或 `smoke_test.py` + `test_web_features.py`）全链路验证。



***

*本文档由核对&#x20;*`C:\word\Graduation Project1`*&#x20;关键文件（config.py/llm.py/rag.py/db.py/main.py/gen\_test\_questions.py/frontend 文案）后撰写（2026-10-14）。本清单即实施依据；尚未执行任何代码改动。*