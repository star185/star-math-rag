# -*- coding: utf-8 -*-
"""基于模板底稿生成《毕业实践综合项目报告书》Word 文档。
原则：克隆模板段落/标题/表格节点，保留样式、自动编号与 TOC 域结构。
"""
import copy
import os
import docx
from docx import Document
from docx.shared import Cm
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.table import Table

SRC = r'C:\word\Graduation Project1\docs\报告书模板_底稿.docx'
DST = r'C:\word\Graduation Project1\毕业实践综合项目报告书_基于RAG的数学知识库智能问答系统.docx'
FIGS = r'C:\word\Graduation Project1\docs\figs'
UISHOTS = r'C:\word\Graduation Project1\ui_shots'

doc = Document(SRC)


def find(prefix):
    for p in doc.paragraphs:
        if p.text.startswith(prefix):
            return p
    raise KeyError('anchor not found: ' + prefix)


def find_in(substr):
    for p in doc.paragraphs:
        if substr in p.text:
            return p
    raise KeyError('anchor not found (contains): ' + substr)


def set_text(p, text):
    runs = p.runs
    if runs:
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
        runs[0].text = text
    else:
        p.add_run(text)
    return p


def set_two_runs(p, tag, content):
    """保留第一个 run 为 tag，第二个 run 为 content，其余删除。"""
    runs = p.runs
    if not runs:
        p.add_run(tag)
        runs = p.runs
    for r in runs[2:]:
        r._element.getparent().remove(r._element)
    runs[0].text = tag
    if len(runs) == 1:
        p.add_run(content)
    else:
        runs[1].text = content
    return p


def clone_p(src_p):
    el = copy.deepcopy(src_p._p)
    return Paragraph(el, src_p._parent)


def insert_after(anchor, *items):
    """items 依次插入到 anchor 之后，保持给定顺序。"""
    for it in reversed(items):
        anchor._p.addnext(it._p)
    return items[-1] if items else anchor


def delete_p(p):
    p._p.getparent().remove(p._p)


# ---------- 段落来源 ----------
H1_SRC = find('概述')
H2_SRC = find('开发目标')          # 文本干净的二级标题
H3_SRC = find('层次方框图')         # 文本干净的三级标题
BODY_SRC = find('××××××××')      # 正文示例段（小4宋体）
CAP_SRC = find('图1.1')            # 图题样式段
TCAP_SRC = find(' 表1.1')          # 表题样式段（原文以空格开头）


def new_body(text):
    p = clone_p(BODY_SRC)
    set_text(p, text)
    return p


def new_heading(text, level):
    src = H1_SRC if level == 1 else (H2_SRC if level == 2 else H3_SRC)
    p = clone_p(src)
    set_text(p, text)
    return p


def new_caption(text):
    p = clone_p(CAP_SRC)
    set_text(p, text)
    return p


def new_tcaption(text):
    p = clone_p(TCAP_SRC)
    set_text(p, text)
    return p


def new_figure(img_path, width_cm):
    p_el = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    jc = OxmlElement('w:jc'); jc.set(qn('w:val'), 'center')
    pPr.append(jc)
    p_el.append(pPr)
    p = Paragraph(p_el, BODY_SRC._parent)
    p.add_run().add_picture(img_path, width=Cm(width_cm))
    return p


def make_para():
    """构造一个空正文段落。"""
    p = clone_p(BODY_SRC)
    if p.runs:
        for r in p.runs[1:]:
            r._element.getparent().remove(r._element)
        p.runs[0].text = ''
    return p


# ---------- 目录条目（TOC 域缓存）处理 ----------
def set_toc_title(p, new_title):
    runs = p.runs
    number_run = None
    tab_idx = None
    for i, r in enumerate(runs):
        if r.text == '\t':
            tab_idx = i
            break
        if number_run is None and r.text.strip():
            number_run = i
    if number_run is None or tab_idx is None:
        return
    title_idxs = list(range(number_run + 1, tab_idx))
    if not title_idxs:
        return
    runs[title_idxs[0]].text = new_title
    for i in title_idxs[1:]:
        runs[i].text = ''


def find_toc(prefix):
    for p in doc.paragraphs:
        if p.style.name.startswith('toc') and p.text.startswith(prefix):
            return p
    raise KeyError('toc entry not found: ' + prefix)


# 更新既有目录条目文本
for old, new in [
    ('1.1. 开发背景（小3号黑体', '1.1. 开发背景'),
    ('2.3.1. 【示例】系统登录', '2.3.1. 用户登录与权限管理'),
    ('2.3.2. 【示例】用户管理', '2.3.2. 知识库管理'),
    ('3.3.3. 用户管理界面设计', '3.3.3. 知识库管理界面设计'),
    ('5.2.1. 【示例】登录测试要点', '5.2.1. 冒烟测试要点'),
    ('5.2.2. 【示例】xxx界面测试要点', '5.2.2. 接口与界面测试要点'),
]:
    set_toc_title(find_toc(old), new)

# 删除示例三级标题的目录条目（1.1.1）
for p in [p for p in doc.paragraphs if p.style.name.startswith('toc')]:
    if p.text.startswith('1.1.1.'):
        delete_p(p)

# 新增目录条目（2.3.3 / 2.3.4 / 3.3.4）
toc_src = find_toc('2.3.2.')
t23_3 = clone_p(toc_src); set_toc_title(t23_3, 'RAG 问答与会话管理')
t23_4 = clone_p(toc_src); set_toc_title(t23_4, '做题练习与评测')
insert_after(toc_src, t23_4, t23_3)
t33_3 = find_toc('3.3.3.')
t33_4 = clone_p(t33_3); set_toc_title(t33_4, '智能问答与做题界面设计')
insert_after(t33_3, t33_4)

# ---------- 封面 ----------
set_text(find('项目名称：'), '项目名称：基于检索增强生成（RAG）的数学知识库智能问答系统')
# 学生姓名/学号/学院/班级/指导教师 保留留空

# ---------- 摘要 ----------
set_text(find('毕业实践综合项目课题名称'), '基于检索增强生成（RAG）的数学知识库智能问答系统的设计与实现')
set_text(find('（空一行）（小4号字体大小'), '')
set_text(find('□□××××××××××××'),
         '本项目设计并实现了基于检索增强生成（Retrieval-Augmented Generation，RAG）技术的数学知识库智能问答系统（Star Math）。系统围绕知识库管理、语义检索、生成回答与交互展示的完整链路展开：后端基于 FastAPI 框架构建，采用父子分块策略解析切分数学语料，构建本地向量索引与 BM25 倒排索引，通过互惠排名融合（RRF）实现向量与关键词混合检索，并以 OpenAI 兼容接口接入 DeepSeek 大模型生成带来源标注的回答；前端采用原生 HTML、CSS、JavaScript 实现登录鉴权、智能问答、知识库管理、检索调试、做题练习与评测等交互界面。系统知识库覆盖数学分析、线性代数、概率统计等模块共 110 篇文档、322 个分块，经 100 道测试题评测，混合检索 Hit@1 达到 0.98，冒烟测试 8/8、接口测试 42/42 全部通过，验证了 RAG 管线在数学问答领域的可行性与可迁移性。')
set_two_runs(find('【关键词】'), '【关键词】', '检索增强生成；数学知识库；混合检索；智能问答；知识库管理')
# 目录区说明文字清空
set_text(find('（空一行，目录要求自动生成）'), '')
set_text(find('目录内容格式：'), '')

# ---------- 第1章 概述 ----------
h2_bg = find('开发背景（小3号黑体')
set_text(h2_bg, '开发背景')
# 删除示例层级标题与占位段
delete_p(find('三级标题（4号黑体'))
delete_p(find('四级标题（小4号黑体'))
delete_p(find('【比如为什么要开发这个系统】'))
# 开发背景正文
b1 = find('××××××××')
set_text(b1, '近年来，以大规模语言模型（LLM）为代表的生成式人工智能技术发展迅速，其在开放域问答、内容生成等任务上表现突出，但存在知识时效性不足、专业领域知识缺失以及"幻觉"（模型编造不实信息）等问题，制约了其在教育等严肃场景下的应用。检索增强生成（Retrieval-Augmented Generation，RAG）通过在生成前检索外部知识库并将相关片段注入提示词，约束模型基于给定材料作答，成为缓解幻觉、实现知识可溯源的主流技术路线。')
b2 = new_body('在高等数学、线性代数、概率统计等大学数学课程的学习过程中，学生常遇到概念辨析、定理理解、解题方法等疑问，需要面向特定教材与讲义、能够给出带依据回答的智能问答工具。通用问答系统缺乏领域语料支撑，回答难以溯源，无法满足教学场景对准确性与可复核性的要求。')
b3 = new_body('本课题要求设计并实现一套数学知识库智能问答系统，需满足六项指标：构建由领域文档与测试题集组成的知识库、实现知识库导入与管理、具备语义检索与 RAG 问答能力、提供交互式 Web 界面、制定并执行测试方案、接入大模型并支持结果溯源。')
insert_after(b1, b2, b3)
# 删除模板示例图表段，插入图1.1
chart_p = None
for p in doc.paragraphs:
    if p.text == '' and p._p.findall('.//' + qn('w:drawing')):
        prev = p._p.getprevious()
        while prev is not None:
            if prev is b3._p:
                chart_p = p
                break
            prev = prev.getprevious()
        if chart_p:
            break
if chart_p is None:
    raise RuntimeError('示例图表段未定位到')
fig1 = new_figure(os.path.join(FIGS, 'fig1_1_rag_route.png'), 15.0)
cap1 = new_caption('图1.1 RAG 技术路线示意图')
insert_after(chart_p, fig1, cap1)
delete_p(chart_p)
# 图后补空行
blank_after = make_para()
insert_after(cap1, blank_after)

# 1.2 开发目标
set_text(find('【该系统需要完成的基本功能'), '本系统以"检索驱动生成、答案可溯源"为总体目标，具体包括：（1）知识库层面：支持多格式文档导入、目录批量导入、父子分块解析与全量重建，形成覆盖数学分析、线性代数、概率统计等模块的领域知识库；（2）检索层面：实现向量检索、关键词检索与混合检索三种模式，通过 RRF 融合与标题加权提升命中率；（3）问答层面：接入大模型实现带引文标注的流式回答，提供会话管理与检索调试能力；（4）交互与评测层面：提供登录鉴权、知识库管理、做题练习、上传解题与评测统计等完整 Web 功能，并配套冒烟、接口与检索效果三层测试体系。')

# 1.3 参考资料
set_text(find('【该系统涉及的开源项目等】'), '系统设计与实现主要参考以下资料：Lewis 等提出的检索增强生成（RAG）框架；Robertson 等提出的 BM25 相关性排序算法；Cormack 等提出的互惠排名融合（RRF）方法；Reimers 等提出的 Sentence-BERT 句向量模型；Vaswani 等提出的 Transformer 注意力机制；开源项目 RAGFlow（InfiniFlow）的知识库检索架构；李航《统计学习方法》（第2版）；同济大学数学系《高等数学》（第7版）；jieba 中文分词工具；FastAPI 官方文档等。具体条目见文末参考文献表。')

# 1.4 设计原则
set_text(find('【设计该系统遵守的原则'), '（1）本地优先：向量索引、BM25 索引与文档解析均在本地完成，不依赖外部向量数据库，便于部署与演示；（2）可配置可扩展：检索模式、Top-K、块大小、模型接口等均以配置文件集中管理，支持一键切换；（3）安全可靠：采用 JWT 鉴权、密码加盐哈希与最小权限原则，关键操作记录日志；（4）领域无关：解析、分块、检索、问答各环节与具体学科解耦，更换语料即可迁移到其他学科；（5）可溯源防幻觉：回答必须携带引文与来源预览，无相关依据时明确提示，不强行作答。')

# 1.5 设计理念（原无正文，插入）
ide_para = find('设计理念')
ide_body = new_body('系统遵循"检索与生成解耦"的理念：检索模块只负责召回与排序，生成模块只负责基于给定上下文作答，二者通过统一的数据结构衔接。分块策略采用"父子分块"——以章节标题切分父块、按段落与句子边界切分子块，检索命中语义更精准的子块，同时携带父块内容提供完整上下文，兼顾召回准确率与回答完整性。回答生成坚持"有据可答、无据不答"，将可溯源作为核心质量指标。')
insert_after(ide_para, ide_body)

# 1.6 设计方法（原无正文，插入）
met_para = find('设计方法')
met_body = new_body('本系统借鉴开源项目 RAGFlow 的知识库检索架构，结合项目体量与教学演示需求进行裁剪改造，采用"文档解析—父子分块—向量化/BM25 索引—混合检索（RRF 融合）—提示词组装—大模型生成"的完整管线。针对数学语料的检索特点实施三项适配——标题精确命中加权、BM25 标题命中优先、混合检索候选集扩大（top_k×4），使混合检索 Hit@1 由初版 0.43 提升至 0.98。开发过程按"需求分析—总体设计—详细设计—编码实现—测试验证"阶段推进，全程以冒烟测试与接口测试保障质量。')
insert_after(met_para, met_body)

# ---------- 第2章 需求分析 ----------
set_text(find('【用平常语言描述该系统的全部功能和细节】'), '功能需求方面，系统需提供：用户注册登录与权限管理（管理员/普通用户）；知识库管理（文档上传、列表、删除、重建、下载、文本预览、目录批量导入）；RAG 智能问答（混合检索、流式回答、引文标注、会话历史）；检索调试（查看命中分块与融合排序过程）；做题练习（按概念/简答/综合/对比题型出题、判分、解析与错题本）；评测（内置 100 道测试题批量评测并统计命中率）；上传解题与习题册管理（识别图片/文本习题、逐题解题、习题册增删改查）。非功能需求方面：回答延迟应满足流式交互体验；系统应支持无外网环境下的降级演示；界面适配常见桌面分辨率；数据与接口具备基本的安全防护。')
set_text(find('【描述具体的操作例子'), '典型使用流程为：用户通过登录页输入账号密码完成鉴权，进入智能问答页面向系统提问；系统检索知识库并经大模型生成带引文的流式回答，用户可点击来源预览查看引用片段；管理员进入知识库管理页面上传或批量导入文档，系统完成解析、分块与索引构建；开发者可进入检索调试页查看关键词、命中分块与 RRF 融合过程；学生可在做题练习页选择题型作答并查看判分与解析，在评测页查看 100 道测试题的整体命中率统计。')
set_text(find('【分析功能并划分功能块】'), '系统按功能划分为八个模块：用户认证模块、知识库管理模块、RAG 问答模块、会话管理模块、检索调试模块、测试题集模块、做题练习模块与评测模块。各模块通过统一接口协同，其中知识库管理与 RAG 问答构成核心链路，各模块职责如下。')

# 2.3.1 / 2.3.2 替换
set_text(find('【示例】系统登录'), '用户登录与权限管理')
set_text(find('【比如，需要实现两种登录模块'), '该模块负责账号体系与访问控制：注册时支持邀请码校验，密码经加盐哈希后存储；登录成功后签发 JWT 令牌（短期访问令牌与长期刷新令牌），前端携带令牌调用接口；接口层通过依赖注入校验令牌，管理员专属接口额外校验角色，未登录访问返回 401。')
set_text(find('【示例】用户管理'), '知识库管理')
set_text(find('【比如：实现用户显示，添加，删除，修改】'), '该模块负责知识库生命周期管理：支持单文件上传与目录批量导入，自动解析文档 frontmatter 并执行父子分块；后台维护文档、分块两级数据，支持列表查询、文本预览、删除（级联清理分块并重建索引）、全量重建与下载；重复文档上传返回 409 拒绝，解析失败给出明确错误提示。')

# 2.3.3 / 2.3.4 新增
h3_anchor = find('知识库管理')  # H3（已改名）
h3_3 = new_heading('RAG 问答与会话管理', 3)
b3_3 = new_body('该模块提供核心问答能力：对用户问题提取中文关键词，同时执行向量检索与 BM25 检索，经 RRF 融合、标题加权与相关性过滤后取 Top-K 片段；将子块内容、父块上下文与系统提示词组装后调用大模型，通过 SSE 协议流式返回回答与引文列表；支持新建、列出、删除会话并保留消息记录，回答延迟与命中来源随消息持久化。')
h3_4 = new_heading('做题练习与评测', 3)
b3_4 = new_body('该模块面向学习与验收场景：内置 100 道测试题（覆盖概念、简答、综合、对比题型，含单选、多选、计算等多种模式），支持按题型随机出题、在线作答、自动判分、解析展示与错题本；评测模块批量运行测试题，统计各检索模式的 Hit@1/Hit@3/Hit@5 与 MRR 指标，输出可复现的评测结果。')
insert_after(h3_anchor, b3_3, h3_3, b3_4, h3_4)

# 2.4 运行环境
env_anchor = find('运行环境')
env_body = new_body('硬件环境：普通个人计算机即可运行（CPU 双核及以上、内存 8GB 及以上、硬盘空间 5GB 以上）；软件环境：Windows 10/11 或 Linux 操作系统、Python 3.10 及以上、Node.js（用于 LLM 网关 mimo）、SQLite 数据库、Chrome/Edge 等现代浏览器；可选依赖：DeepSeek 大模型 API 密钥（无密钥时系统自动降级为本地抽取模式）。')
insert_after(env_anchor, env_body)

# ---------- 第3章 总体设计 ----------
sys_body = new_body('系统采用 B/S 架构，自顶向下划分为表现层、应用层、检索层与数据层四层。表现层为原生 Web 前端，负责登录鉴权、智能问答、知识库管理、检索调试、做题练习与评测等页面；应用层为 FastAPI 后端，提供鉴权、知识库、问答、会话、评测、做题与习题册等 REST/SSE 接口；检索层封装文档解析、父子分块、向量化、BM25 索引与混合检索；数据层以 SQLite 存储业务数据，配合本地向量索引与 BM25 倒排索引提供检索支撑。')
insert_after(find('系统建模'), sys_body)

# 3.1.1 层次方框图
set_text(find('【从顶部开始，按照层次分类进行细化】'), '系统层次结构如图 3.1 所示。')
h_anchor = find('系统层次结构如图')
fig31 = new_figure(os.path.join(FIGS, 'fig3_1_hierarchy.png'), 15.0)
cap31 = new_caption('图3.1 系统层次方框图')
blank = make_para()
insert_after(h_anchor, fig31, cap31, blank)

# 3.2 类图设计
set_text(find('【使用UML画出各个类的属性、继承和方法】'), '后端采用模块化设计，核心类包括：Config（集中管理检索、模型与习题参数）、main（FastAPI 路由入口）、Security（密码哈希与 JWT 令牌）、Parser（文档解析与父子分块）、VectorStore（向量索引、BM25 索引与 RRF 融合检索）、LLM（提示词组装与流式调用，支持本地抽取回退）、DB（数据库连接与表操作）以及 RAG（检索与问答编排）。RAG 依赖 VectorStore 与 LLM，各业务路由依赖 DB 与 Security，核心模块类图如图 3.2 所示。')
cl_anchor = find_in('核心模块类图如图')
fig32 = new_figure(os.path.join(FIGS, 'fig3_3_class.png'), 15.0)
cap32 = new_caption('图3.2 后端核心模块类图')
blank = make_para()
insert_after(cl_anchor, fig32, cap32, blank)

# 3.3 接口设计
set_text(find('【各个子系统之间的接口和用户接口】'), '系统接口分为前端与后端接口（REST/SSE）、后端模块间调用两类。前端与后端通过 HTTP/JSON 通信，问答接口采用 SSE 实现流式输出；模块间采用函数级调用，main 路由调用 RAG、DB、Security 等模块完成业务编排。主要接口如表 3.1 所示。')
set_text(find('【各个部件是通过何种方式进行连接'), '各模块通过以下方式连接：前端经 HTTP 请求调用 FastAPI 路由，问答与逐题解题接口经 SSE 长连接接收流式输出；后端模块以 Python 函数调用方式衔接，检索相关模块通过共享的 Config 参数协同；数据库统一经 db.py 访问 SQLite，避免业务层直接操作数据。主要接口定义见表 3.1。')

# 表3.1 系统主要接口一览（克隆模板表并裁剪为4列）
tbl_el = None
for child in doc.element.body.iterchildren():
    if child.tag == qn('w:tbl'):
        tbl_el = child
        break
if tbl_el is None:
    raise RuntimeError('模板表格未找到')
TBL_SRC_EL = copy.deepcopy(tbl_el)
tbl_el.getparent().remove(tbl_el)   # 移除模板示例空表（后续表格均由克隆生成）


def trim_table(tbl, ncols):
    """裁剪表格到 ncols 列（逐行删 tc + 删 gridCol）。"""
    grid = tbl.find(qn('w:tblGrid'))
    if grid is not None:
        gcs = grid.findall(qn('w:gridCol'))
        for gc in gcs[ncols:]:
            grid.remove(gc)
    for tr in tbl.findall(qn('w:tr')):
        tcs = tr.findall(qn('w:tc'))
        for tc in tcs[ncols:]:
            tr.remove(tc)
    return tbl


def cell_text(tc):
    p = tc.find(qn('w:p'))
    return ''.join(t.text or '' for t in p.findall('.//' + qn('w:t')))


def set_cell(tc, text, align=None):
    p = tc.find(qn('w:p'))
    runs = p.findall(qn('w:r'))
    for r in runs[1:]:
        p.remove(r)
    if runs:
        r0 = runs[0]
        ts = r0.findall(qn('w:t'))
        for t in ts[1:]:
            r0.remove(t)
        if ts:
            ts[0].text = text
        else:
            t = OxmlElement('w:t'); t.text = text
            r0.append(t)
    else:
        r = OxmlElement('w:r')
        t = OxmlElement('w:t'); t.text = text
        r.append(t)
        p.append(r)
    if align is not None:
        pPr = p.find(qn('w:pPr'))
        if pPr is None:
            pPr = OxmlElement('w:pPr')
            p.insert(0, pPr)
        jc = pPr.find(qn('w:jc'))
        if jc is None:
            jc = OxmlElement('w:jc')
            pPr.append(jc)
        jc.set(qn('w:val'), align)


def new_table_from_src(ncols, header, rows, aligns=None):
    """基于模板表克隆新表：header 为表头列表，rows 为数据行列表。"""
    tbl = Table(copy.deepcopy(TBL_SRC_EL), doc)
    trim_table(tbl._tbl, ncols)
    trs = tbl._tbl.findall(qn('w:tr'))
    header_tr, sample_tr, empty_tr = trs[0], trs[1], trs[2]
    hcs = header_tr.findall(qn('w:tc'))
    for i, htxt in enumerate(header):
        set_cell(hcs[i], htxt, 'center')
    header_tr.getparent().remove(sample_tr)
    current = tbl._tbl.findall(qn('w:tr'))
    last_tr = current[-1]
    for r in rows:
        new_tr = copy.deepcopy(empty_tr)
        last_tr.addnext(new_tr)
        last_tr = new_tr
    trs2 = tbl._tbl.findall(qn('w:tr'))
    for ri, r in enumerate(rows):
        tcs = trs2[ri + 1].findall(qn('w:tc'))
        for ci, val in enumerate(r):
            a = aligns[ci] if aligns else None
            set_cell(tcs[ci], str(val), a)
    return tbl


def insert_after_block(anchor, item):
    """在段落或表格之后插入段落或表格。"""
    anchor_el = anchor._tbl if isinstance(anchor, Table) else anchor._p
    item_el = item._tbl if isinstance(item, Table) else item._p
    anchor_el.addnext(item_el)


api_rows = [
    (1, 'POST', '/api/auth/register', '用户注册（邀请码校验）'),
    (2, 'POST', '/api/auth/login', '用户登录，签发 JWT'),
    (3, 'GET', '/api/auth/me', '获取当前用户信息'),
    (4, 'POST', '/api/docs/upload', '上传知识库文档'),
    (5, 'GET', '/api/docs', '文档列表'),
    (6, 'POST', '/api/docs/{id}/rebuild', '重建文档索引'),
    (7, 'POST', '/api/docs/{id}/delete', '删除文档'),
    (8, 'GET', '/api/search/debug', '检索调试（关键词/命中分块/排序）'),
    (9, 'POST', '/api/conversations', '新建会话'),
    (10, 'POST', '/api/chat', '智能问答（SSE 流式回答）'),
    (11, 'GET', '/api/conversations/{id}/messages', '会话消息列表'),
    (12, 'POST', '/api/eval/run', '批量评测（100 道测试题）'),
    (13, 'GET', '/api/eval/metrics', '评测统计指标'),
    (14, 'GET', '/api/test-questions', '获取测试题集'),
    (15, 'POST', '/api/practice/next', '随机出题'),
    (16, 'POST', '/api/practice/submit', '提交判分'),
    (17, 'POST', '/api/exercises/upload', '上传解题/习题册'),
    (18, 'GET', '/api/health', '健康检查'),
]
api_header = ['序号', '方法', '路径', '功能说明']
api_tbl = new_table_from_src(4, api_header, api_rows, aligns=['center', 'center', 'left', 'left'])
tcap31 = new_tcaption('表3.1 系统主要接口一览')
anchor = find_in('主要接口定义见表 3.1')
insert_after(anchor, tcap31)
insert_after_block(tcap31, api_tbl)
blank = make_para()
insert_after_block(api_tbl, blank)

# 3.3.2 登录界面设计
set_text(find('登录界面设计'), '登录界面设计')
login_body = new_body('登录界面包含账号、密码输入框与登录按钮，支持回车提交；输入校验不合格时给出提示，登录成功后跳转至智能问答页。登录界面如图 3.3 所示。')
h_anchor = find('登录界面设计')
insert_after(h_anchor, login_body)
fig33 = new_figure(os.path.join(UISHOTS, '00_before_login.png'), 14.5)
cap33 = new_caption('图3.3 用户登录界面')
blank = make_para()
insert_after(login_body, fig33, cap33, blank)

# 3.3.3 知识库管理界面设计（改名 + 插入正文与截图）
set_text(find('用户管理界面设计'), '知识库管理界面设计')
kb_body = new_body('知识库管理界面提供文档上传、目录批量导入、文档列表、文本预览、删除与全量重建操作，并对文档状态（解析中/就绪/失败）进行标识。知识库管理界面如图 3.4 所示。')
h_anchor = find('知识库管理界面设计')
insert_after(h_anchor, kb_body)
fig34 = new_figure(os.path.join(UISHOTS, '03_after_kb.png'), 14.5)
cap34 = new_caption('图3.4 知识库管理界面')
blank = make_para()
insert_after(kb_body, fig34, cap34, blank)

# 3.3.4 智能问答与做题界面设计（新增）
h3_anchor = find('知识库管理界面设计')  # 已改名的 H3
h3_5 = new_heading('智能问答与做题界面设计', 3)
qa_body = new_body('智能问答界面支持会话切换、问题输入、流式回答展示与来源引用预览，回答中自动标注 [1][2] 等引文并可展开查看来源片段；做题练习界面支持题型选择、在线作答、判分与解析展示。分别如图 3.5、图 3.6 所示。')
fig35 = new_figure(os.path.join(UISHOTS, '02_after_chat.png'), 14.5)
cap35 = new_caption('图3.5 智能问答界面')
blank1 = make_para()
fig36 = new_figure(os.path.join(UISHOTS, '05_after_practice.png'), 14.5)
cap36 = new_caption('图3.6 做题练习界面')
blank2 = make_para()
insert_after(h3_anchor, qa_body, h3_5, fig35, cap35, blank1, fig36, cap36, blank2)

# 3.4 数据库结构设计
set_text(find('【主要是描述】'), '系统数据库采用 SQLite（backend/storage/app.db），依据 3.2 节实体关系设计，共 9 张数据表：users、documents、chunks、conversations、messages、eval_records、practice_records、exercise_sources、exercise_items。其中 documents 记录文档元信息（含分块数、所属书、篇、页码），chunks 保存分块内容、向量索引与父块上下文（父子分块），messages 持久化回答来源与延迟。数据库表清单见表 3.2，users 表结构见表 3.3。')
set_text(find('数据库E-R图'), '数据库E-R图')
er_body = new_body('数据库 E-R 关系已在 3.2 节整体描述，实体关系如图 3.7 所示，本节给出数据库表的逻辑结构设计。')
h_anchor = find('数据库E-R图')
insert_after(h_anchor, er_body)
fig37 = new_figure(os.path.join(FIGS, 'fig3_2_er.png'), 15.0)
cap37 = new_caption('图3.7 系统实体关系（E-R）图')
blank = make_para()
insert_after(er_body, fig37, cap37, blank)

# 表3.2 数据库表一览
db_rows = [
    (1, 'users', '用户账号、密码哈希与角色信息'),
    (2, 'documents', '知识库文档元信息（chunk_count、book、part、pages、状态）'),
    (3, 'chunks', '分块数据（内容、向量索引、父块内容与父块索引）'),
    (4, 'conversations', '会话记录'),
    (5, 'messages', '会话消息（来源 JSON、延迟毫秒）'),
    (6, 'eval_records', '检索评测记录'),
    (7, 'practice_records', '做题练习记录'),
    (8, 'exercise_sources', '上传习题来源（习题册）'),
    (9, 'exercise_items', '习题条目（题目、答案、解析、判分状态）'),
]
db_tbl = new_table_from_src(3, ['序号', '数据表', '说明'], db_rows, aligns=['center', 'center', 'left'])
tcap32 = new_tcaption('表3.2 数据库表一览')
anchor = find_in('数据库表清单见表 3.2')
insert_after(anchor, tcap32)
insert_after_block(tcap32, db_tbl)
blank = make_para()
insert_after_block(db_tbl, blank)

# 表3.3 users 表结构（8列模板原样）
tpl_header = [cell_text(tc) for tc in tbl_el.findall(qn('w:tr'))[0].findall(qn('w:tc'))]
user_rows = [
    (1, 'id', 'INTEGER', '—', '无', '否', '是', '自增主键'),
    (2, 'username', 'TEXT', '64', '无', '否', '否', '登录用户名（唯一）'),
    (3, 'password_hash', 'TEXT', '128', '无', '否', '否', '加盐哈希后的密码'),
    (4, 'role', 'TEXT', '16', 'user', '否', '否', '角色：admin / user'),
    (5, 'created_at', 'TEXT', '32', '无', '否', '否', '创建时间'),
]
user_tbl = new_table_from_src(8, tpl_header, user_rows, aligns=['center', 'center', 'center', 'center', 'center', 'center', 'center', 'left'])
tcap33 = new_tcaption('表3.3 users 用户表结构')
anchor = find_in('users 表结构见表 3.3')
insert_after(anchor, tcap33)
insert_after_block(tcap33, user_tbl)
blank = make_para()
insert_after_block(user_tbl, blank)

# 3.5 出错处理
set_text(find('【描述如果出错的处理方法】'), '系统针对典型异常制定了处理策略：大模型接口未配置或调用超时/失败时，自动降级为本地抽取模式并向用户提示；JWT 令牌过期时返回 401，前端引导用户重新登录并自动续期；文档重复上传时返回 409 并提示；文档解析失败时标记该文档状态并给出错误信息；检索无相关片段时，回答明确提示"根据现有知识库资料无法确定"，避免模型编造；批量导入遇个别文件失败时跳过并汇总失败清单。')

# 3.6 安全保密设计
set_text(find('【描述采用何种方法保证安全性】'), '系统采用多重安全措施：口令安全——用户密码使用加盐哈希存储，不保存明文；访问控制——基于 JWT 的令牌鉴权，访问令牌短时有效、刷新令牌长时有效，接口按角色授权（管理员专属接口校验 admin 角色）；密钥管理——SECRET_KEY 在首次启动时随机生成并持久化保存；注册控制——注册接口要求邀请码，防止未授权注册；数据安全——上传文档仅对登录用户可见，删除操作需权限校验；传输安全——前端统一携带 Authorization 头访问受保护接口。')

# ---------- 第4章 详细设计 ----------
set_text(find('【具体来说就是把经过总体设计得到的各个模块详细的加以描述。】'), 'RAG 问答处理的程序流程如图 4.1 所示：接收用户问题后先做中文有效性校验，随后提取中文 2~6 字关键词，并行执行向量检索与 BM25 检索（含标题加权），经 RRF 融合、相关性过滤与 Top-K 截断得到候选片段，组装包含子块与父块上下文的提示词，调用大模型经 SSE 流式生成回答并附引文，前端渲染回答与来源卡片。知识库文档入库流程如图 4.2 所示。')
anchor = find_in('知识库文档入库流程如图 4.2 所示')
fig41 = new_figure(os.path.join(FIGS, 'fig4_1_rag_flow.png'), 15.0)
cap41 = new_caption('图4.1 RAG 问答处理流程图')
blank = make_para()
fig42 = new_figure(os.path.join(FIGS, 'fig4_2_ingest_flow.png'), 15.0)
cap42 = new_caption('图4.2 知识库文档入库流程图')
blank2 = make_para()
insert_after(anchor, fig41, cap41, blank, fig42, cap42, blank2)

# 4.2 伪代码编写
pseudo_intro = '核心检索与问答编排伪代码如下（编码阶段已转化为代码注释与实现）：'
pseudo_lines = [
    'function retrieve(question, mode):',
    '    tokens = jieba.lcut(extract_keywords(question))            // 中文关键词提取与分词',
    '    if mode == vector:',
    '        candidates = vector_search(question, top_k * 4)        // 候选扩大',
    '    else if mode == keyword:',
    '        candidates = bm25_search(tokens, top_k * 4)',
    '    else:                                                      // hybrid 混合检索',
    '        vec = vector_search(question, top_k * 4)',
    '        bm  = bm25_search(tokens, top_k * 4)',
    '        candidates = rrf_merge(vec, bm)                        // 互惠排名融合',
    '        candidates = title_boost(candidates)                   // 标题命中加权',
    '    hits = relevance_filter(candidates)                        // 相关性过滤',
    '    return top_k(hits)                                         // 返回 Top-K 分块',
    '',
    'function answer_stream(question, conversation_id):',
    '    hits = retrieve(question, config.retrieval_mode)',
    '    if hits is empty:',
    '        yield SSE(done, "根据现有知识库资料无法确定")           // 防幻觉拒答',
    '        return',
    '    messages = build_messages(question, hits, system_prompt)   // 子块+父块上下文',
    '    for token in llm.stream_chat(messages):                    // SSE 流式输出',
    '        yield SSE(delta, token)',
    '    yield SSE(done, {sources: hits})                           // 引文列表',
    '',
    '// 父子分块：按章节标题切父块，按段落与句子边界切子块，子块携带父块内容入库',
]
set_text(find('【使用中文或者英文进行伪代码编写'), pseudo_intro)
anchor = find('核心检索与问答编排伪代码如下')
items = [new_body(line) for line in pseudo_lines]
insert_after(anchor, *items)

# ---------- 第5章 实现 ----------
# 5.1.1 代码约定
set_text(find('代码约定'), '代码约定')
anchor = find('代码约定')
cb = new_body('系统后端采用 Python 3.10+ 与 FastAPI 框架，代码遵循以下约定：模块按职责划分（config、db、rag、llm、vector_store、security、main 等）；配置项集中在 config.py，可通过环境变量覆盖；数据库操作统一封装在 db.py，禁止业务层直接拼写 SQL；日志统一输出到服务端日志文件；接口遵循 RESTful 风格，问答类接口使用 SSE 事件流；关键参数（Top-K、块大小、加权系数）以常量集中管理。')
insert_after(anchor, cb)

# 5.1.2 代码编写原则
set_text(find('代码编写原则'), '代码编写原则')
anchor = find('代码编写原则')
pb = new_body('（1）分层解耦：表现层、应用层、检索层、数据层之间只通过明确定义的接口交互；（2）错误降级：大模型调用失败时回退本地抽取模式，检索无结果时返回防幻觉提示；（3）参数化设计：检索模式、Top-K、加权系数等通过配置切换，避免硬编码；（4）数据一致性：分块入库采用事务，重建索引支持全量替换；（5）可维护性：核心算法（BM25、RRF 融合）独立实现并配套注释，便于评审与复用。')
insert_after(anchor, pb)

# 5.2.1 冒烟测试要点
set_text(find('【示例】登录测试要点'), '冒烟测试要点')
set_text(find('【描述该如何测试，数据的输入，类型】'), '冒烟测试覆盖系统核心启动链路，共 8 项全部通过：后端服务启动、前端页面加载、健康检查接口、登录接口、知识库文档上传、检索调试、智能问答流式回答、做题练习出题。每项以真实请求验证返回码与关键字段，确保系统可用后再进行全量测试。')

# 5.2.2 接口与界面测试要点
set_text(find('【示例】xxx界面测试要点'), '接口与界面测试要点')
anchor = find('接口与界面测试要点')
it_body = new_body('接口测试按八个模块共 42 个用例全覆盖：认证模块 9 例（注册、登录、鉴权、权限等）、RAG 问答模块 3 例、会话管理 6 例、知识库管理 10 例、检索调试 3 例、测试题集 2 例、做题练习 7 例、评测模块 2 例，全部通过；浏览器界面测试覆盖登录、问答、知识库、检索调试、做题、评测等页面在主流分辨率下的可用性，全部通过。')
insert_after(anchor, it_body)

# 5.3 测试结果和总结
anchor = find('测试结果和总结')
t5_body1 = new_body('各模块接口测试统计如表 5.1 所示，三种检索策略的评测结果如表 5.2 所示。')
insert_after(anchor, t5_body1)

mod_rows = [
    (1, '用户认证', 9, 9, '通过'),
    (2, 'RAG 问答', 3, 3, '通过'),
    (3, '会话管理', 6, 6, '通过'),
    (4, '知识库管理', 10, 10, '通过'),
    (5, '检索调试', 3, 3, '通过'),
    (6, '测试题集', 2, 2, '通过'),
    (7, '做题练习', 7, 7, '通过'),
    (8, '评测', 2, 2, '通过'),
    ('合计', '', 42, 42, '全部通过'),
]
mod_tbl = new_table_from_src(5, ['序号', '测试模块', '用例数', '通过数', '结果'], mod_rows,
                             aligns=['center', 'center', 'center', 'center', 'center'])
tcap51 = new_tcaption('表5.1 各模块接口测试统计')
insert_after(t5_body1, tcap51)
insert_after_block(tcap51, mod_tbl)
blank = make_para()
insert_after_block(mod_tbl, blank)

ev_rows = [
    ('向量检索', '0.97', '0.98', '0.99', '0.9775'),
    ('关键词检索（BM25）', '0.96', '0.98', '1.00', '0.9733'),
    ('混合检索（hybrid）', '0.98', '0.99', '1.00', '0.9858'),
]
ev_tbl = new_table_from_src(5, ['检索模式', 'Hit@1', 'Hit@3', 'Hit@5', 'MRR'], ev_rows,
                            aligns=['center', 'center', 'center', 'center', 'center'])
tcap52 = new_tcaption('表5.2 三种检索策略评测结果')
insert_after(blank, tcap52)
insert_after_block(tcap52, ev_tbl)
blank2 = make_para()
insert_after_block(ev_tbl, blank2)
sum_body = new_body('测试结果表明：系统功能完整、接口稳定，冒烟测试 8/8、接口测试 42/42 全部通过；混合检索在各项指标上全面优于单一检索策略，Hit@1 达 0.98、MRR 达 0.9858。混合检索的优势源于 RRF 融合、标题加权与候选扩大等语料适配措施，使数学领域专业问题能够稳定命中正确知识片段，达到课题设定的检索效果目标。')
insert_after(blank2, sum_body)

# ---------- 第6章 维护 ----------
set_text(find('维护方法'), '维护方法')
anchor = find('维护方法')
m1 = new_body('系统提供一键脚本便于日常维护：start.bat 启动后端与前端服务，stop.bat 停止服务，smoke.bat 执行冒烟测试；服务运行日志统一写入日志文件便于排障；LLM 访问令牌有效期为 24 小时，演示或答辩前需执行续期命令刷新令牌；知识库支持单文档删除与全量重建，数据备份通过复制数据库文件完成。')
insert_after(anchor, m1)
set_text(find('维护文档'), '维护文档')
anchor = find('维护文档')
m2 = new_body('项目维护文档包括：README.md（目录结构、一键启动方式、默认账号、知识库导入命令、接口说明）、docs 目录（开题报告、功能检测报告、检索评测说明）、后端源码注释与伪代码注释，覆盖部署、使用、测试与二次开发四类维护场景。')
insert_after(anchor, m2)
set_text(find('功能拓展方法'), '功能拓展方法')
anchor = find('功能拓展方法')
m3 = new_body('（1）学科迁移：更换知识库语料并重建索引即可迁移到其他学科；（2）解析增强：替换文档解析引擎可支持更多格式与扫描件（OCR）；（3）判分闭环：exercise_items 已预留判分状态字段，可扩展自动批改与学情统计；（4）检索升级：可替换为远程 Embedding 服务或外部向量数据库，适配更大规模语料；（5）模型扩展：通过 OpenAI 兼容接口接入其他大模型，支持按需切换。')
insert_after(anchor, m3)

# ---------- 第7章 作品特色及不足 ----------
anchor = find('作品特色及不足')
feat = new_body('作品特色：（1）检索效果显著——针对数学语料实施标题加权、BM25 标题命中优先与候选扩大三项适配，混合检索 Hit@1 由初版 0.43 提升至 0.98；（2）防幻觉可溯源——回答强制携带引文与来源预览，无依据时明确拒答；（3）父子分块设计——子块召回精准、父块补充上下文，兼顾准确率与完整性；（4）流式交互——基于 SSE 的流式回答提升交互体验；（5）本地可演示——不依赖外部数据库，离线即可运行；（6）功能闭环——内置 100 道测试题评测与做题练习，形成"构建—检索—问答—评测"完整链路。')
short = new_body('存在的不足：（1）窄屏（小于 960px）下部分导航被隐藏，移动端适配不完整；（2）favicon 缺失，浏览器标签页无站点图标；（3）线性代数语料覆盖存在部分缺口，个别问题回答"无法确定"；（4）LLM 访问令牌 24 小时有效期，需在演示前手动续期；（5）评测过程为顺序执行、耗时较长，未做并发优化。以上问题将在后续版本中逐步完善。')
insert_after(anchor, feat, short)

# ---------- 第8章 总结 ----------
anchor = find('总结')
sum8 = new_body('本项目完成了基于检索增强生成的数学知识库智能问答系统（Star Math）的设计、实现与测试。系统采用"解析—分块—索引—混合检索—生成"的 RAG 完整管线，融合向量检索与 BM25 关键词检索，针对数学语料进行了标题加权与候选扩大适配，并通过父子分块提升上下文完整性；围绕知识库管理、智能问答、做题练习、评测等场景实现了完整的 Web 交互。经测试，系统冒烟测试与 42 项接口测试全部通过，混合检索 Hit@1 达 0.98，验证了技术路线的可行性与有效性。通过本次实践，深入掌握了检索增强生成、混合检索融合、前后端联调与系统测试的方法，为后续在更大规模知识库与更多学科上的应用打下了基础。')
insert_after(anchor, sum8)

# ---------- 参考资料 ----------
refs = [
    '[1] LEWIS P, PEREZ E, PIKTUS A, et al. Retrieval-augmented generation for knowledge-intensive NLP tasks[C]//Advances in Neural Information Processing Systems 33 (NeurIPS 2020). Vancouver: Curran Associates, 2020: 9459-9474.',
    '[2] ROBERTSON S E, ZARAGOZA H. The probabilistic relevance framework: BM25 and beyond[J]. Foundations and Trends in Information Retrieval, 2009, 3(4): 333-389. DOI: 10.1561/1500000019.',
    '[3] CORMACK G V, CLARKE C L A, BÜTTCHER S. Reciprocal rank fusion outperforms Condorcet and individual rank learning methods[C]//Proceedings of the 32nd International ACM SIGIR Conference on Research and Development in Information Retrieval. Boston: ACM, 2009: 758-759. DOI: 10.1145/1571941.1572114.',
    '[4] REIMERS N, GUREVYCH I. Sentence-BERT: sentence embeddings using Siamese BERT-networks[C]//Proceedings of the 2019 Conference on Empirical Methods in Natural Language Processing and the 9th International Joint Conference on Natural Language Processing (EMNLP-IJCNLP). Hong Kong: Association for Computational Linguistics, 2019: 3982-3992. DOI: 10.18653/v1/D19-1410.',
    '[5] VASWANI A, SHAZEER N, PARMAR N, et al. Attention is all you need[C]//Advances in Neural Information Processing Systems 30 (NIPS 2017). Long Beach: Curran Associates, 2017: 5998-6008.',
    '[6] INFINIFLOW. RAGFlow: an open-source RAG-based AI engine[EB/OL]. [2026-10-14]. https://github.com/infiniflow/ragflow.',
    '[7] 李航. 统计学习方法[M]. 2版. 北京: 清华大学出版社, 2019.',
    '[8] 同济大学数学系. 高等数学: 上册[M]. 7版. 北京: 高等教育出版社, 2014.',
    '[9] FXSJY. jieba: 结巴中文分词[EB/OL]. [2026-10-14]. https://github.com/fxsjy/jieba.',
    '[10] FASTAPI. FastAPI framework documentation[EB/OL]. [2026-10-14]. https://fastapi.tiangolo.com.',
]
ref_paras = [p for p in doc.paragraphs if p.style.name == '参考文献']
for rp, newtext in zip(ref_paras, refs[:len(ref_paras)]):
    set_text(rp, newtext)
# 模板仅有 9 条参考文献占位，第 10 条克隆补写
if len(refs) > len(ref_paras):
    last = ref_paras[-1]
    for extra in refs[len(ref_paras):]:
        np = clone_p(last)
        set_text(np, extra)
        insert_after(last, np)
        last = np

# ---------- 致谢 ----------
anchor = find('致谢（单独一页）')
ack = new_body('本毕业实践项目从选题、方案设计到系统实现与测试，历时数月，在此向所有给予帮助的人致以诚挚的谢意。感谢指导教师在课题方向、技术路线与报告撰写中给予的悉心指导；感谢学院提供良好的实践环境；感谢在开发与调试过程中给予帮助的同学；感谢家人一直以来的理解与支持。本项目的完成是多方帮助与个人努力共同作用的结果，我会将这段宝贵的实践经历转化为继续前行的动力。')
insert_after(anchor, ack)
# 移除模板致谢占位段（××××）
for p in doc.paragraphs:
    if p.text.startswith('□□××××××××××××××××××××××××'):
        delete_p(p)
        break

# 删除模板遗留的示例表题占位段
for p in doc.paragraphs:
    if p.text.lstrip().startswith('表1.1'):
        delete_p(p)
        break

doc.save(DST)
print('SAVED:', DST)
print('paragraphs:', len(doc.paragraphs))
