# -*- coding: utf-8 -*-
"""重绘毕业实践报告书全部示意图（v2）：
- 统一风格：大画布 + 高 DPI + 大字号 + 充足留白，杜绝文字重叠/挤压
- 修正数据：知识库基线 110 篇文档 / 323 分块（据功能检测报告）
- 输出 docs/figs/*.png
"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib import font_manager

# ---------- 中文字体 ----------
SIMHEI = r'C:\Windows\Fonts\simhei.ttf'
if os.path.exists(SIMHEI):
    font_manager.fontManager.addfont(SIMHEI)
    plt.rcParams['font.sans-serif'] = ['SimHei']
else:
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

OUT = r'C:\word\Graduation Project1\docs\figs'
os.makedirs(OUT, exist_ok=True)

# ---------- 通用工具 ----------
C = {
    'box': '#5B8FF9', 'box2': '#61C0A8', 'box3': '#F6BD16', 'box4': '#E8684A',
    'note': '#8C9EFF', 'line': '#4A5B77', 'text': '#222222', 'hl': '#C94F3D',
    'soft': '#DDE7FF', 'soft2': '#DDF3EA', 'soft3': '#FDF3D3',
}


def box(ax, x, y, w, h, text, fc='#F4F7FC', ec=C['box'], lw=2, fs=13, bold=False,
        ha='center', va='center', rounding=0.02):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad=0.006,rounding_size={rounding}",
                       fc=fc, ec=ec, lw=lw, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha=ha, va=va, fontsize=fs,
            color=C['text'], fontweight='bold' if bold else 'normal', zorder=5)


def arrow(ax, x1, y1, x2, y2, color=C['line'], lw=1.8, rad=0.0, style='-|>',
          ls='-', mutation=20, zorder=4):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=mutation,
                        linewidth=lw, color=color, linestyle=ls,
                        connectionstyle=f"arc3,rad={rad}", zorder=zorder)
    ax.add_patch(a)


def label(ax, x, y, text, fs=11.5, color=C['text'], bold=False):
    ax.text(x, y, text, fontsize=fs, color=color, ha='center', va='center',
            fontweight='bold' if bold else 'normal',
            bbox=dict(boxstyle='round,pad=0.18', fc='white', ec='none', alpha=0.9),
            zorder=6)


def newfig(w, h):
    fig, ax = plt.subplots(figsize=(w, h), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis('off')
    return fig, ax


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print('fig saved:', path)


# ============================================================
# 图1.1 RAG 技术路线图（110 篇文档 / 323 分块）
# ============================================================
fig, ax = newfig(15.6, 8.0)
bx, by, bw, bh = 5, 36, 20, 22
steps = [
    ('用户问题', C['soft3'], C['box3']),
    ('中文校验\n（2-6 字滑窗）', C['soft'], C['box']),
    ('混合检索\n向量 / BM25 / 标题加权', C['soft'], C['box']),
    ('RRF 融合\n（互惠排名）', C['soft2'], C['box2']),
    ('相关性过滤\nTop-K 排序', C['soft2'], C['box2']),
    ('提示词组装\n（父块/子块上下文）', C['soft'], C['box']),
    ('大模型\nDeepSeek · SSE 流式', C['soft3'], C['box3']),
]
xs = [3, 18.6, 34.2, 49.8, 65.4, 81, 96.6]
for i, (x, (t, fc, ec)) in enumerate(zip(xs, steps)):
    box(ax, x, 60, 16.5 if i == 6 else 13.5, 24, t, fc=fc, ec=ec,
        fs=11.5 if i >= 5 else 12.5, bold=True)
for i in range(len(xs) - 1):
    arrow(ax, xs[i] + 13.5, 72, xs[i + 1], 72, rad=0.0)
# 底部：知识库 + 检索层回注
box(ax, 3, 12, 17, 20, '数学知识库\n110 篇文档 / 323 分块\n（分析 72·线代 21·概率 17）',
    fc=C['soft'], ec=C['box'], fs=12, bold=True)
arrow(ax, 11.5, 32, 11.5, 59, rad=0.0)
box(ax, 33, 12, 17, 20, '向量索引\nBM25 倒排索引\n本地 Embedder', fc=C['soft'], ec=C['box'], fs=12, bold=True)
arrow(ax, 41.5, 32, 41.5, 59, rad=0.0)
box(ax, 78, 12, 17, 20, '回答 + [n] 引文\n来源文档预览', fc=C['soft2'], ec=C['box2'], fs=12, bold=True)
arrow(ax, 86.5, 59, 86.5, 33, rad=0.0)
label(ax, 50, 94, '基于 RAG 的数学知识库智能问答技术路线', fs=17, bold=True)
ax.set_xlim(0, 114.5)  # 末框右缘 113.1，必须扩出 100，否则被坐标轴裁剪只剩一小条
save(fig, 'fig1_1_rag_route.png')

# ============================================================
# 图3.1 系统层次方框图
# ============================================================
fig, ax = newfig(15.6, 8.2)
layers = [
    ('展现层', '前端页面：登录鉴权 · 智能问答 · 知识库管理 · 检索调试 · 做题练习 · 学习/习题册', C['box']),
    ('应用层', '用户鉴权 · 会话管理 · 知识库管理 · RAG 问答 · 评测 · 做题 · 上传解题/习题册', C['box2']),
    ('检索解析层', '父子分块 · 向量化 · BM25 索引 · 混合检索（RRF 融合）· 相关性过滤排序', C['box3']),
    ('数据层', 'SQLite 数据库 · 本地向量索引 · BM25 倒排索引 · 上传文件存储', C['box4']),
]
ys = [76, 56, 36, 16]
for (name, desc, ec), y in zip(layers, ys):
    box(ax, 3, y, 16, 12, name, fc='#F4F7FC', ec=ec, fs=14, bold=True)
    box(ax, 22, y, 75, 12, desc, fc='#FBFCFE', ec=ec, fs=13.5)
    if y > 16:
        ax.plot([11, 59.5], [y, y - 0.5], color=C['line'], lw=1.6, zorder=2)
        arrow(ax, 11, y - 0.5, 59.5, y - 13, rad=0.0)
label(ax, 50, 94, '系统总体层次结构', fs=17, bold=True)
save(fig, 'fig3_1_hierarchy.png')

# ============================================================
# 图3.2 E-R 图（9 实体，标题置顶、字段下置、按真实外键布线）
# 外键关系（据 db.py）：users→conversations/practice_records/exercise_sources/exercise_items
#   conversations→messages, documents→chunks, exercise_sources→exercise_items
#   eval_records 无外键，独立标注
# ============================================================
fig, ax = newfig(16.8, 11.6)

entities = {
    'users': ('用户', ['id(主键)', 'username', 'password_hash', 'salt', 'role', 'created_at'], (2, 76)),
    'conversations': ('会话', ['id(主键)', 'user_id', 'title', 'created_at'], (36, 76)),
    'messages': ('消息', ['id(主键)', 'conversation_id', 'role', 'content', 'sources_json'], (70, 76)),
    'exercise_sources': ('习题来源', ['id(主键)', 'user_id', 'name', 'source_type', 'question_count'], (2, 42)),
    'exercise_items': ('习题条目', ['id(主键)', 'source_id', 'qno', 'type', 'question', 'answer', 'solution', 'status'], (36, 42)),
    'practice_records': ('练习记录', ['id(主键)', 'user_id', 'qid', 'module', 'qtype', 'correct'], (70, 42)),
    'documents': ('文档', ['id(主键)', 'name', 'format', 'size', 'chunk_count', 'status'], (2, 8)),
    'chunks': ('分块', ['id(主键)', 'doc_id', 'content', 'section', 'vector_id', 'parent_content'], (36, 8)),
    'eval_records': ('评测记录', ['id(主键)', 'question', 'mode', 'answer', 'sources_json', 'latency_ms'], (70, 8)),
}
EW, EH = 26, 30
for key, (zh, fields, (x, y)) in entities.items():
    # 实体标题带：置顶
    FancyBboxPatch((x, y + EH - 8.5), EW, 8.5,
                   boxstyle='round,pad=0.006,rounding_size=0.02',
                   fc=C['soft'], ec=C['box'], lw=2, zorder=3)
    ax.add_patch(FancyBboxPatch((x, y + EH - 8.5), EW, 8.5,
                                boxstyle='round,pad=0.006,rounding_size=0.02',
                                fc=C['soft'], ec=C['box'], lw=2, zorder=3))
    ax.text(x + EW / 2, y + EH - 4.2, zh, fontsize=15, ha='center', va='center',
            color=C['text'], fontweight='bold', zorder=5)
    # 主体框
    FancyBboxPatch((x, y), EW, EH - 8.5,
                   boxstyle='round,pad=0.006,rounding_size=0.02',
                   fc='white', ec=C['box'], lw=2, zorder=2)
    ax.add_patch(FancyBboxPatch((x, y), EW, EH - 8.5,
                                boxstyle='round,pad=0.006,rounding_size=0.02',
                                fc='white', ec=C['box'], lw=2, zorder=2))
    # 字段
    fld = '\n'.join(fields)
    ax.text(x + EW / 2, y + (EH - 8.5) / 2 - 1.2, fld, fontsize=11.5,
            ha='center', va='center', color=C['text'], zorder=5)

# 关系连线（1/N 端点标注带白底）
anchor = {
    'users': (2, 76), 'conversations': (36, 76), 'messages': (70, 76),
    'exercise_sources': (2, 42), 'exercise_items': (36, 42), 'practice_records': (70, 42),
    'documents': (2, 8), 'chunks': (36, 8), 'eval_records': (70, 8),
}
rels = [
    ('users', 'conversations', '1', 'N', 'r'),
    ('conversations', 'messages', '1', 'N', 'r'),
    ('users', 'exercise_sources', '1', 'N', 'd'),
    ('exercise_sources', 'exercise_items', '1', 'N', 'r'),
    ('users', 'exercise_items', '1', 'N', 'd'),
    ('users', 'practice_records', '1', 'N', 'e'),
    ('documents', 'chunks', '1', 'N', 'r'),
]
for a, b, la, lb, kind in rels:
    axa, aya = anchor[a]
    axb, ayb = anchor[b]
    if kind == 'r':  # 同行横向
        x1, y1 = axa + EW, aya + EH / 2
        x2, y2 = axb, ayb + EH / 2
        rad = 0.0
    elif kind == 'd':  # 短对角（下一行相邻列）
        x1, y1 = axa + EW / 2, aya
        x2, y2 = axb + EW / 2, ayb + EH
        rad = 0.10
    else:  # 'e' 肘形绕行（users 底 → 右侧 → 下行）
        x1, y1 = axa + EW / 2, aya
        xm = axb + EW / 2
        # 先向下，再水平至目标列上方，再向下
        ax.plot([x1, x1], [y1, ayb + EH + 5], color=C['line'], lw=1.8, zorder=2)
        ax.plot([x1, xm], [ayb + EH + 5, ayb + EH + 5], color=C['line'], lw=1.8, zorder=2)
        ax.plot([xm, xm], [ayb + EH + 5, ayb + EH], color=C['line'], lw=1.8, zorder=2)
        ax.add_patch(FancyArrowPatch((xm, ayb + EH + 4.5), (xm, ayb + EH + 0.3),
                                     arrowstyle='-|>', mutation_scale=20, linewidth=1.8,
                                     color=C['line'], zorder=4))
        label(ax, (x1 + xm) / 2, ayb + EH + 8.2, lb, fs=11, bold=True)
        label(ax, x1, (y1 + ayb + EH + 5) / 2, la, fs=11, bold=True)
        continue
    arrow(ax, x1, y1, x2, y2, rad=rad)
    dx, dy = x2 - x1, y2 - y1
    d = max(abs(dx), abs(dy))
    off = 3.0
    label(ax, x1 + (dx / d) * off, y1 + (dy / d) * off, la, fs=11, bold=True)
    label(ax, x2 - (dx / d) * off, y2 - (dy / d) * off, lb, fs=11, bold=True)

# 评测记录独立标注
ax.text(83, 6, '评测记录为独立表\n（无外键，按 question 关联评测）', fontsize=10.5,
        color='#777777', ha='center', va='top', zorder=6)
label(ax, 50, 108.5, '系统数据库实体关系（E-R）图', fs=17, bold=True)
ax.set_ylim(0, 112)  # 第一行实体标题带顶 106，必须扩出 100，否则顶部三个实体框被坐标轴裁剪
save(fig, 'fig3_2_er.png')

# ============================================================
# 图3.3 后端核心模块类图（9 模块 3×3）
# ============================================================
fig, ax = newfig(16.8, 10.4)
mods = {
    'Config': ('Config\n配置管理', (2, 78)),
    'main': ('main\nAPI 路由主服务', (36, 78)),
    'Security': ('Security\n安全鉴权', (70, 78)),
    'Parser': ('Parser\n文档解析', (2, 46)),
    'VectorStore': ('VectorStore\n向量/BM25 索引', (36, 46)),
    'LLM': ('LLM\n大模型接入', (70, 46)),
    'DB': ('DB\n数据访问', (2, 14)),
    'RAG': ('RAG 编排\nretrieve/answer_stream', (36, 14)),
    'Exercise': ('Exercise\n上传解题/习题册', (70, 14)),
}
MW, MH = 26, 20
for key, (t, (x, y)) in mods.items():
    box(ax, x, y, MW, MH, t, fc='#F4F7FC', ec=C['box'], fs=13.5, bold=True)
# 主要调用关系
arrow(ax, 62, 88, 70, 88, rad=0.0)                    # main→Security
label(ax, 66, 91.5, '鉴权中间件', fs=10.5)
arrow(ax, 49, 77, 15, 67, rad=0.12)                   # main→Parser
label(ax, 30, 68, '文档解析', fs=10.5)
arrow(ax, 49, 77, 49, 67, rad=0.0)                    # main→VectorStore
label(ax, 52.5, 72.5, '混合检索', fs=10.5)
arrow(ax, 62, 77, 83, 67, rad=0.12)                   # main→LLM
label(ax, 74, 68, '流式生成', fs=10.5)
arrow(ax, 49, 76, 42, 35, rad=0.14)                   # main→RAG（左上绕行）
label(ax, 40, 54, '检索编排', fs=10.5)
arrow(ax, 62, 76, 77, 35, rad=0.14)                   # main→Exercise
label(ax, 73, 54, '上传解题', fs=10.5)
arrow(ax, 49, 76, 15, 24, rad=0.16, ls='--')          # main→DB（虚线）
label(ax, 30, 44, '数据访问', fs=10.5)
arrow(ax, 62, 35, 83, 67, rad=0.16, ls='--')          # RAG→LLM
label(ax, 79, 52, '提示词组装', fs=10.5)
arrow(ax, 49, 46, 49, 35, rad=0.0, ls='--')           # VectorStore→RAG
label(ax, 52.5, 40.5, '检索调用', fs=10.5)
arrow(ax, 15, 78, 36, 88, rad=0.10, ls='--')          # Config→main
label(ax, 26, 86, '配置注入', fs=10.5)
label(ax, 50, 97, '后端核心模块类图', fs=17, bold=True)
save(fig, 'fig3_3_class.png')

# ============================================================
# 图4.1 RAG 问答处理流程图
# ============================================================
fig, ax = newfig(16.0, 8.6)
flow = [
    '接收问题',
    '中文校验\n（防噪声输入）',
    '关键词提取\n（2-6 字滑窗）',
    '混合检索\n（向量 + BM25\n+ 标题加权）',
    'RRF 融合\n（互惠排名）',
    '相关性过滤\n+ Top-K 排序',
    '提示词组装\n（父块/子块上下文）',
    'SSE 流式生成\n（DeepSeek）',
]
xs = [2, 16.4, 30.8, 45.2, 59.6, 74, 88.4, 102.8]
for i, (x, t) in enumerate(zip(xs, flow)):
    box(ax, x, 52, 13, 24, t, fc=C['soft'] if 'RRF' not in t and '过滤' not in t else C['soft2'],
        ec=C['box'] if 'RRF' not in t and '过滤' not in t else C['box2'],
        fs=10.5 if i == 5 else 11.5, bold=True)
for i in range(len(xs) - 1):
    arrow(ax, xs[i] + 13, 64, xs[i + 1], 64, rad=0.0)
box(ax, 2, 14, 13.2, 18, '回答 + [n] 引文\n来源预览', fc=C['soft3'], ec=C['box3'], fs=12, bold=True)
arrow(ax, 8.6, 52, 8.6, 33, rad=0.0)
label(ax, 50, 92, 'RAG 问答处理流程', fs=17, bold=True)
ax.set_xlim(0, 119)  # 末框右缘 115.8，必须扩出 100，否则第8框整个被裁剪（"最后一个背景没有了"）
save(fig, 'fig4_1_rag_flow.png')

# ============================================================
# 图4.2 知识库文档入库流程图
# ============================================================
fig, ax = newfig(16.0, 9.4)
ing = [
    ('1', '文档上传\n/ 批量导入目录'),
    ('2', '格式解析（Markdown）\n+ frontmatter 读取'),
    ('3', '父子分块\n（标题切父块，段落\n句子边界拆子块）'),
    ('4', '子块向量化\n（本地 Embedder）\n+ BM25 索引构建'),
    ('5', '事务入库\n（documents / chunks）\n+ 状态置 ready'),
    ('6', '检索调试接口\n定向命中验证'),
]
ys = [82, 67, 52, 37, 22, 7]
for (no, t), y in zip(ing, ys):
    ax.text(3, y + 9, no, fontsize=16, fontweight='bold', color=C['hl'], ha='center', va='center', zorder=6)
    box(ax, 7, y, 36, 13, t, fc=C['soft'], ec=C['box'], fs=12, bold=True)
    if y > 7:
        arrow(ax, 25, y + 13, 25, y + 13.9, rad=0.0)
# 右侧：异常与管理
box(ax, 52, 68, 42, 14, '重复文档上传\n→ 409 拒绝', fc='#FDF1EF', ec=C['box4'], fs=12.5, bold=True)
arrow(ax, 43, 66, 51.8, 73, rad=0.10)
box(ax, 52, 34, 42, 14, '管理员删除文档\n→ 级联删除分块并重建索引', fc='#FDF1EF', ec=C['box4'], fs=12.5, bold=True)
arrow(ax, 43, 42, 51.8, 39, rad=0.10)
label(ax, 50, 96, '知识库文档入库流程', fs=17, bold=True)
save(fig, 'fig4_2_ingest_flow.png')

print('ALL FIGURES DONE')
