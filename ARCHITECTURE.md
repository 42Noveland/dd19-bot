# dd19 QQ 机器人 · 架构与实现详解

> 版本：2026-10-03（精选移植收官版）｜ 代码量：core 3,074 行 + plugins 1,221 行 ｜ 测试：112 单测 + 14 探针 + 全链路 e2e
> 本文档讲解**每个功能是什么、怎么实现的、为什么这么做**。用户向的配置/使用说明见 `README.md`。

---

## 0. 总览

### 0.1 它是什么

一个跑在本机 Windows 上的 QQ 群聊天机器人：QQ 小号 **123456789**（群昵称 dd19，默认人设「十九」），
在 3 个白名单群里像朋友一样聊天——**看得到**群里的图和消息（感知），**学得会**风格/黑话/关系（认知），
**判断得了**什么时候该接话（决策），**表达得出**文字/表情包/引用（表达），并且有一整套人设/记忆/情绪/好感度的"人格底座"。

### 0.2 技术栈

| 部件 | 选型 | 说明 |
|---|---|---|
| 协议端 | NapCat（Node 版） | QQ 纯 shell 核心，OneBot v11 反向 WS 连 bot；自带 WebUI 管理 |
| 框架 | NoneBot2（Python） | 插件式事件处理；本机 venv 隔离 |
| 存储 | SQLite（WAL） | `bot/data/context.db`，12 张表，纯 sqlite3 薄封装 |
| 主模型 | opencode_go → deepseek-v4.1-flash | 思考档 medium；system/dynamic 分层注入保前缀缓存 |
| 视觉 | opencode_go deepseek-v4-flash-vision-exp | 本地 MiniCPM-V（llama.cpp :8082）兜底 |
| 搜索 | Firecrawl API | LLM 按需工具调用（function calling） |
| 测试 | pytest + 自写探针 | 单测不碰真实网络；探针走假 NapCat 连接做活体验证 |

### 0.3 设计原则（贯穿全项目）

1. **不影响整体环境**：不装系统服务、不改 PATH/注册表、不动用户自己的 QQ；一切在 `qqbot/` 内。
2. **廉价预筛优先**：能用本地计算（正则/bigram/统计）解决的，绝不调 LLM；LLM 只花在"必须理解语义"的地方。
3. **降级永远静默**：记忆/情绪/贴图/黑话/好感度任何一环出错，都只影响那一环，聊天本身照常。
4. **一切可回滚**：人设补充、表情包选择、好感度都支持管理命令纠偏；学习系统只提议、不擅自改人设。
5. **零第三方依赖倾向**：核心层只用标准库 + nonebot + httpx；黑话分词用 bigram 而非 jieba。

### 0.4 一条消息的完整旅程（架构图）

```
群友消息
  │
  ├─▶ observer (priority=45) ──▶ context.db.messages 入库（@渲染/去重）
  │        └─ 有图 → 异步任务：NapCat本地缓存取图 → vision 描述 → 回填 "[图片: 描述]"
  │
  ├─▶ basic (10)     /ping /jrrp /help ──▶ 直接回复
  │
  ├─▶ llm_chat (50)  @我 或 文字@ ──▶ chat_flow：
  │        限频 → 记忆检索 → 组装动态块（风格/黑话/好感度/心情/图库清单/接话注记）
  │        → llm.chat（人设 system + 动态块拼用户消息尾部 + 工具循环）
  │        → 记账 + 记录回复 → 引用发送
  │
  └─▶ auto_chat (48) 没被@ ──▶ 预筛 → 概率门 → LLM 判定 → 接话（静默复用 chat_flow）

后台循环（各管一摊，互不阻塞）：
  memory_loop  每2分钟：提炼记忆        mood_loop    每10分钟：整理心情
  style_loop   每5分钟：提取风格对      jargon_loop  每10分钟：黑话预筛+推断
  persona_evo_loop 每天：生成人设补充建议（待审）
```

---

## 1. 基础设施层

### 1.1 NapCat 协议端（`napcat/NapCat.Shell.Node/`）

- Node 版 NapCat 自带 QQ 核心（无需桌面 QQ），`napcat.bat` = `node.exe ./index.js`。
- **反向 WS**：bot 监听 `127.0.0.1:8081/onebot/v11/ws`，NapCat 主动连过来（断线自动重连，bot 重启无需动 NapCat）。
- **WebUI**（127.0.0.1:6099）：登录态、配置管理；token 在 `napcat/config/webui.json`。
- **本地 HTTP API**（127.0.0.1:3000，经 WebUI API 动态增删）：调试口，`get_image`（返回本地缓存路径）、`send_group_msg`（真机测试用）。
- **关键坑**：OneKey 一键包内置下载链接 404；Node 包缺 `crypto.dll`/`ssl.dll`（从同版本 QQ 安装目录复制）；这两条都踩过并有脚本工具（`scripts/check-pe-deps.py`）。

### 1.2 NoneBot2 插件体系（`plugins/`）

插件按 **priority** 排序执行，这是整个消息路由的骨架：

| 优先级 | 插件 | 职责 | 阻塞 |
|---|---|---|---|
| 5 | llm_chat 的 /model /persona | 管理命令 | block |
| 10 | basic | /ping /jrrp /help | block |
| 20 | /chat /search /usage /memory /mood /style /jargon /affection | 命令 | block |
| **45** | **observer** | **记录全部消息（必须先于聊天）** | 不阻塞 |
| **48** | **auto_chat** | **主动接话判断** | 不阻塞 |
| **50** | **llm_chat 的 chat_all** | **默认聊天（@我即聊）** | 不阻塞 |

- observer(45) 先于 chat_all(50)：保证"触发消息"先入库，回复组装上下文时按 message_id 剔除它。
- auto_chat(48) 在 observer 后、聊天前：判断"要不要接"，接了就复用聊天流程。
- `event_preprocessor`（llm_chat 内）：在命令解析前把**文字形式 "@昵称/@QQ号"** 剥掉并置 `event.to_me=True`——兼容 QQ 客户端没生成真实 @ 段的情况（`strip_text_mention`，含 "@dd190" 误触发防护）。

### 1.3 配置系统（`core/config.py` + `.env`）

- **三层默认值**：代码 dataclass 默认值 → `.env` 覆盖 → `os.environ` 覆盖（`load_config` 合并）。
- 进程级单例 `get_config()`；改 `.env` 需重启（或按功能热更新：人设文件按 mtime 热更新）。
- 55 个配置项分 9 组：基础/LLM/搜索/上下文/视觉/贴图/接话/记忆/情绪/风格/黑话/好感度/人格演化。
- **测试隔离铁律**：测试里 `monkeypatch core.config._config`，否则用例随真实 `.env` 漂移。

### 1.4 LLM 层（`core/llm.py`，359 行）

**chat_once（单后端一次请求 + 工具循环）**：

1. **system 组装**：`system_prompt_override`（按群人设）或默认人设；`extra_system`（稳定规则）拼进 system。
2. **动态块拼装**：`dynamic_blocks`（随消息变化的上下文）拼在**用户消息尾部**——
   这样 system 保持稳定，**最大化 LLM 前缀缓存命中率**（省 token、降延迟）。
3. **单次会话上限**：输入估算 + 输出上限合计 ≤ `LLM_MAX_TOKENS`（10w），超长自动截断（先保人设+消息+动态块）。
4. **工具循环**（最多 `LLM_TOOL_MAX_ROUNDS`+1 轮）：
   - 携带 web_search（按需）+ 调用方附加工具（如 send_sticker）；
   - **不变量 ①**：模型一轮发多个 tool_call 时必须**逐个**回填 `role:"tool"`（漏一个下一轮 400）；
   - **不变量 ②**：最后一轮**强制不带 tools**，逼出最终回答（否则模型可能每轮都想继续搜）；
   - 工具报错 → 去掉 tools 重试一次（兼容不认 tools 的后端）。
5. **思考与正文分离**：`reasoning_content` 或内联 ` thinking` 都会被拆出；空正文抛 `EmptyReplyError`。
6. **记账**：每轮 usage 计入 budget；额度耗尽抛 `QuotaExceededError`。

**chat（回退链）**：主选 → `LLM_FALLBACKS` 依次尝试；配额错误不触发回退（全局状态）。

**opencode_go 细节**：需要 `x-opencode-session` 请求头（缺则 400）；思考用 `reasoning_effort` 参数。

### 1.5 Token 预算（`core/budget.py`）

- **单次会话上限**：`LLM_MAX_TOKENS`（10w）= 输入估算 + 输出上限，超了截断输入。
- **单日上限**：`LLM_DAILY_TOKEN_LIMIT`（1kw），**北京时间跨天自动重置**，持久化到 `logs/token-usage.json`（重启不丢），按 provider 分账。
- 额度耗尽时 @bot 只回复「白饭吃完了QAQ」（`LLM_QUOTA_REPLY` 可改）；`/usage` 查当日用量。

### 1.6 联网搜索（`core/search.py` + llm_chat）

- **LLM 按需调用**（不是固定指令）：天气/新闻/价格类问题模型自己决定调 `web_search` 工具；结果回填后继续生成，回复附来源链接。
- `/search 关键词` 是手动兜底（同一套 Firecrawl 搜索 + LLM 总结 + 链接）。
- 每轮最多执行 2 个搜索调用（超出的也回填"跳过"说明，保持历史合法）。

---

## 2. 感知层

### 2.1 消息观察者（`plugins/observer.py`）

- 白名单群**所有消息**静默入库（`messages` 表）：谁、什么时间、文本、媒体、message_id。
- **@ 渲染**：`gate.render_message_text` 把 @ 段渲染成 "@昵称"（昵称经 `get_group_member_info` 解析+进程缓存）——
  适配器自带的 `extract_plain_text` 会丢掉 @ 段，导致句中的 @ 对 LLM 不可见（实测踩过："问谁呀"）。
- 开头的连续 @机器人（称呼本身）剥掉；"all" → @全体成员。
- 重复投递按 message_id 去重；机器人自己的消息在回复时记录（避免双记）。
- **7 天滚动**：每 200 次写入触发一次清理（保留 7 天 + 每群最多 5000 条）。

### 2.2 图片识别管道（`observer._caption_later` + `core/vision.py`）

- 收到图 → 立即记 `[图片]` 占位 → 异步任务：
  1. **优先 NapCat 本地缓存**：`get_image(file=…)` 返回本地 md5 路径（QQ CDN 链接短时效、过期会 400 且失败静默——铁律）；
  2. 云 vision（deepseek-v4-flash-vision-exp）生成描述，本地 MiniCPM-V 兜底；
  3. 描述**回填**消息文本（`[图片]` → `[图片: 描述]`），同时作为**贴图库底账**（images 表，md5 去重，重复图零成本）。
- vision `max_tokens ≥ 800`：vision 模型先思考，给小了正文被挤空（踩过）。
- 失败静默降级：识别失败不影响聊天，只在日志留痕。

---

## 3. 表达层

### 3.1 聊天回复流程（`llm_chat.chat_flow`，公共核心）

所有聊天（@触发 / 主动接话 / /chat）都走同一条流程：

```
总开关检查 → 配额检查 → 限频检查（每人 5s）
→ 记忆检索（mem_block：本人+@提及+名字出现+话题相关）
→ 组装 prompt（最近 12 条群聊 + 记忆 + 当前消息；主动接话场景措辞不同）
→ 组装动态块（dynamic_blocks）：
    ① 风格参考（相似场合的 few-shot 样例）
    ② 黑话理解（含已知黑话时）
    ③ 关系（好感度等级+语气建议）
    ④ 你现在的状态（心情）
    ⑤ 表情包提示 + 图库清单
    ⑥ 场合注记（主动接话时的"没人@你"提示）
→ llm.chat（人设 system + 动态块拼用户尾部 + 工具循环）
→ 记录自己的回复（记录名 = 该群人设名）→ 引用发送
```

- 主动接话场景带 `quiet_skip=True`：配额/限频/失败一律静默，绝不刷提示。
- 好感度在 LLM 调用**前**结算：回复就能带上"刚夸完你"的最新关系。

### 3.2 引用回复（`QUOTE_REPLY_ENABLED`）

- 聊天回复自动带 `MessageSegment.reply(event.message_id)`——一眼看清在回谁。
- 命令类回复保持干净不引用。

### 3.3 表情包系统（`core/stickers.py`，169 行）

**四件套：收集 → 索引 → 选择 → 发送**：

- **收集/索引**：images 表（群图片+vision 描述）即图库；caption 就是索引。
- **选择算法**（`pick`）：
  1. 候选 = 有描述且文件存在的图；
  2. 防重：同群 10 分钟内发过的排除（`repeat=true` 或小图库兜底除外）；
  3. 打分：query 与 caption 的 **CJK bigram + ASCII 词**重合数 → 排序，**人气（seen_count）× 新鲜度**做加成；
  4. **同义情绪词扩展**（`_MOOD_GROUPS`）：query "开心" 能命中含"高兴"的图；
  5. 无匹配 → 按新鲜度加权随机（宁发勿尬）；全无 → None。
- **热度衰减**（借鉴 self-learning）：`_freshness` 15 天二次曲线 1.0→0.2——久未出现的图降权但**不删除**（images 表兼任 caption 缓存）。
- **发送**：`MessageSegment.image(file:///本地路径)`（NapCat 支持），每轮回复最多 1 张；发出后以 `[表情包: 描述]` 记入群上下文。
- **自然配图引导**：`chat_hint()` 提示"觉得配一张更带感就调 send_sticker，频率约 3~5 条回复最多 1 张"；图库清单（人气×新鲜度重排的 12 条）附在提示里供模型挑 query。

---

## 4. 决策层（`plugins/auto_chat.py`，146 行）

**"没人 @ 他也会接话"** 的实现——四级漏斗，宁缺毋滥：

1. **预筛（零成本）**：跳过 to_me/命令/自己消息；每群冷却 `AUTO_REPLY_COOLDOWN`（默认 240s）；90 秒内刚说过话不抢话。
2. **概率门**：普通消息 `AUTO_REPLY_CHANCE`（35%）抽样；**提到机器人名字/昵称的消息必判**（豁免抽样）。
3. **LLM 判断**：给最近群聊 + 新消息，让模型输出"接/不接"（第一行含"接"且不含"不接"才算接；解析失败按不接——保守）。
4. **静默执行**：判定接 → 复用 `chat_flow(quiet_skip=True, addressed=False)`，回复带引用、可配表情包。

同一群同时只判一条（`_inflight` 锁）；失败日志含异常类型+堆栈。

---

## 5. 认知层（五个学习系统）

### 5.1 记忆系统（`core/memory.py` + `memory_loop`，283 行）

- **提炼**：后台每 2 分钟检查，积压 ≥10 条才提炼（每轮每群 ≤3 批）；一次 LLM 调用把一批消息提炼成
  `{"kind":"user","name":…,"fact":…}`（成员事实）和 `{"kind":"group","fact":…}`（群事件）；容错解析（容忍代码块/垃圾行）。
- **水位**：`mem_watermark` 记录已处理到的消息行号——增量、失败不推水位（下轮重试）、首次运行自动"补课"全部历史。
- **上限**：每人 40 条 / 每群 80 条，超限淘汰最旧；同文本去重刷新时间。
- **多路检索**（`for_prompt`，零额外 token）：说话人本人 + @ 提及的人（`extra_user_ids`）+ 消息里出现名字的人（复合名拆分）+ 话题相关（bigram≥2）——"聊到谁就想起谁"。

### 5.2 情绪状态（`core/mood.py` + `mood_loop`，141 行）

- 每群一条心情（平静/开心/得意/无语/委屈/恼火/疲惫/好奇），**半衰期 2400s** 指数衰减，<0.2 归平静。
- 后台每 10 分钟、群里 ≥6 条新互动才整理一次：LLM 看最近互动输出 `{"mood":…,"intensity":…,"reason":…}`。
- 注入【你现在的状态】块（语气自然带一点，别刻意强调）；发图时综合"心情×话题×对话内容"挑图。
- `/mood` 查看、`/mood set 得意 0.9 被夸了` 手动设置、`/mood update` 立即整理。

### 5.3 风格学习（`core/style_pairs.py` + `style_loop`，199 行｜借鉴 self-learning）

- **机械提取（零 LLM）**：`messages` 表里相邻的「用户行 → Bot 行」即一个风格样本
  （situation ≤40 字 / expression ≤90 字）；只认真实 bot QQ 的回复行；贴图行不打断配对；命令/媒体/过短过滤。
- **水位增量**：`style_watermark`；上线即自动补课历史（实测 180 行→13 对）。
- **去重**：UNIQUE(群,人设,situation,expression)，复发刷新时间+权重小涨。
- **人设隔离**：每个样本记录生成它的人设名——Elena 的样本不会注入给十九的回复。
- **注入**：bigram 相似度 >0 才注入（最多 3 条）："【风格参考】你以前在类似场合的回复（模仿语气，不要照抄）"。
- **衰减/清理**：15 天新鲜度曲线参与排序；30 天未复发删除；每群上限 300。

### 5.4 黑话学习（`core/jargon.py` + `jargon_loop`，269 行｜借鉴 self-learning）

- **零 LLM 预筛**：扫最近 7 天消息分词（CJK bigram + ASCII 词），**常用字过滤**（杀"来张/是十"跨词碎片）+ **四信号合成**：
  跨群 IDF(0.35) + 突发频率(0.25) + 用户集中度(0.2) + **字符互信息 PMI(0.2，区分真词与碎片)**；频次门槛 5。
- **LLM 推断**：每轮取候选（**排除已入表的——rejected 也排除，不重复问**）最多 6 个，附上下文例子批量问含义；
  `slang=false` 记 rejected。实测：朋友群从真实聊天学到「海克/克斯」≈海克斯的简写。
- **注入纪律**（原文抄 self-learning）："以下黑话解释只用于理解用户当前消息。回复时**不要主动复读、模仿、扩散**，
  也不要把解释原样输出；仅在用户明确询问含义时才说明。"
- `/jargon` 查看、`/jargon mine` 立即挖掘。

### 5.5 好感度（`core/affection.py` + `affection_cmd`，176 行｜借鉴 self-learning）

- **规则分类零 LLM**：夸赞 +5 / 感谢 +2 / 关心 +2 / 普通聊天 +1 / 不耐烦 -5 / 攻击 -10（关键词表，先查负面）。
- **心情修正**：正向变化 ×(0.5~1.2)（心情好时夸一句涨得多）。
- **守恒制**：单用户上限 100；**群总上限 250**——给谁加分超出部分从其他人逐轮扣 1/4（"精力有限"）。
- **自然衰减**：每 2 天未互动 -1（在下次互动时一次性结算）。
- **只在 directed 消息结算**（`addressed=True` 才更新——主动接话不算）；回复带【关系】块。
- `/affection` 查看排行、`/affection set <QQ> <值>` 设置。

---

## 6. 人格系统

### 6.1 按群人设（`core/personas.py`，125 行）

- **人设 = md 文件**：`bot/persona*.md`（十九/Sparkle/Elena），文件按 **mtime 缓存**——改 md 即热更新。
- **按群选择**：`personas` 表存每群的文件 stem；无记录用默认（`LLM_PERSONA_FILE`）。
- **`/persona <名字>`**（管理员）：切换立即生效（每次回复实时解析，无需重启）；`/persona default` 恢复默认。
- **resolve(group)** → (名字, 文本)：chat_flow / 判定 / 记忆 / 心情 全按群取人设；bot 消息的记录名 = 该群人设名。
- SillyTavern 卡适配：`tools/card2persona.py` 转换草稿 + **人工群聊适配**（去 NSFW/1v1、去叙事格式、补行为准则）。

### 6.2 人格演化审查（`core/persona_evo.py` + `persona_evo_loop`，249 行｜借鉴 self-learning）

- **生成**：以本群风格对（15 条）+ 记忆（10 条）+ 当前人设为证据，LLM 提"人设补充建议"（口语化命令式，最多 3 条）；
  去重（不重复待审/已生效）；每群待审上限 10；触发条件：≥15 条新风格对（没人聊就不打扰）。
- **审查**：`/persona review` 看待审 → `approve <id>` 批准 / `reject <id>` 拒绝。
- **生效**：批准写入 `persona_patches`；`personas.resolve` 组装时拼在**人设文本尾部**——
  **persona md 原文分毫不动**，`/persona patch rm <id>` 随时回滚。
- 实测质量：从真实聊天提炼出"被人纠正时别急着认错，先查证再用来源说话"（来自它翻 Wiki 的那场）、"点单表情包按情绪现挑，没有就直说欠着改天补"（来自那张欠的图）。

---

## 7. 数据层（`core/context.py`，345 行，12 张表）

| 表 | 用途 | 生命周期 |
|---|---|---|
| `messages` | 群消息（含图片描述回填） | 7 天滚动 + 每群 5000 条上限 |
| `images` | 图片 md5 缓存 + 贴图库底账 | 永久（兼 caption 缓存，不删） |
| `memories` | 长期记忆（user/group 两类） | 永久；40/人、80/群上限淘汰 |
| `mem_watermark` | 记忆提炼水位 | 每群一行 |
| `mood` | 每群心情 | 每群一行，衰减归平静 |
| `personas` | 每群人设选择 | 每群一行 |
| `style_pairs` | 风格样本（few-shot） | 30 天未复发删除；300/群上限 |
| `style_watermark` | 风格提取水位 | 每群一行 |
| `jargon` | 黑话（known/rejected） | rejected 30 天清理；known 80/群上限 |
| `affection` | 好感度 | 每群每人一行 |
| `persona_proposals` | 人设补充建议（待审/批准/拒绝） | 每群待审上限 10 |
| `persona_patches` | 已生效的人设补充 | 手动回滚删除 |

- **存储引擎**：原生 sqlite3 + WAL（跨进程读写安全——探针/工具直接读库）；`_lock` 保证线程安全；进程级单连接。
- **原则**：只增不改（除水位/回填/衰减）；清理策略都写在各自的模块里，不搞全局 GC。

---

## 8. 命令与运维

### 8.1 命令全表

| 命令 | 权限 | 功能 |
|---|---|---|
| `/ping` `/jrrp` `/help` | 所有人 | 在线测试 / 今日人品 / 菜单 |
| `@我 或 /chat <内容>` | 所有人 | 聊天（默认 mention 模式） |
| `/search <词>` | 所有人 | 联网搜索（手动兜底） |
| `/usage` | 所有人 | 今日 token 用量 |
| `/model` | 管理员 | 查看/切换模型、测试后端 |
| `/persona` | 管理员 | 查看/切换人设；`review/approve/reject/evolve/patches/patch rm` 学习补充审查 |
| `/memory` | 管理员 | 记忆统计；`extract` 立即提炼 |
| `/mood` | 管理员 | 心情查看；`set` 手动设置；`update` 立即整理 |
| `/style` | 管理员 | 风格库统计；`extract` 立即提取 |
| `/jargon` | 管理员 | 黑话库查看；`mine` 立即挖掘 |
| `/affection` | 管理员 | 好感度排行；`set <QQ> <值>` 设置 |

### 8.2 启动 / 重启 / 日志

- NapCat：`napcat\NapCat.Shell.Node\napcat.bat`（等"已登录成功"）；bot：`bot\start-bot.cmd`（独立窗口，含端口检测一键重启）。
- 日志：`bot/logs/bot-YYYY-MM-DD.log`（按天文件，任何启动方式都写）；`/model test` 排后端问题。
- **安全重启**：kill 前看日志——若最后一条入站消息后还没有 `running complete`，说明还在处理（带搜索可达 1 分钟），等它跑完再重启。

### 8.3 测试体系

- **112 个单测**（`pytest -q tests`，~3s）：全部隔离（monkeypatch 配置单例、临时 DB、假 transport），不碰真实网络。
- **14 个活体探针**（`tests/e2e/probe_*.py`）：用假 NapCat 连接（假 self_id 防 403）走真实链路——引用/表情包/情绪/接话/记忆×2/人设切换/风格/黑话/好感度/人格演化/@解析/感知/自然配图。
- **全链路 e2e**（`fake_napcat.py`）：模拟群友发命令/聊天/搜索，断言消息段（引用 id 精确匹配，免疫接话串扰），期望 ALL PASS。
- 探针约定：确定性断言 + 打印供检视；统计性探针（自然配图/接话）接受"模型本轮有权不动作"。

---

## 9. 关键设计决策与踩坑精华（为什么这么做）

### 9.1 三个"保命"工程决策

1. **注入顺序保前缀缓存**：动态上下文拼用户消息尾部、system 只放人设+固定规则 → LLM API 前缀缓存命中（省 token/降延迟）。
2. **图片获取铁律**：QQ CDN 链接短时效会 400 且静默 → 必须走 NapCat 本地缓存（`get_image`）；重试上限、失败留痕。
3. **工具循环两条不变量**：多 tool_call 必须逐个回填（否则 400）；最后强制不带 tools 逼出终答。

### 9.2 认知系统的共同范式

```
廉价预筛（本地计算） → 批量 LLM（只在必要时） → 容错解析 → 水位/去重/上限 → 注入时最小化
```
- 记忆：≥10 条积压才提炼；黑话：四信号预筛省 70-80% 调用；风格：零 LLM 纯机械；
- 所有 LLM 输出都是**容错解析**（容忍代码块/垃圾行）；所有后台循环失败都静默降级。

### 9.3 踩坑速查（改代码前必读）

| 坑 | 症状 | 解法 |
|---|---|---|
| 合成事件缺 `font` 字段 | 静默回落基类，命令永不匹配 | 探针事件必须带 `font: 0` |
| 重复 `X-Self-ID` | 握手 403 | 探针用假 self_id（10001+） |
| 文字 @ 兼容 | QQ 客户端未生成 @ 段 | `event_preprocessor` 剥文字 @ 并置 to_me |
| @ 段对 LLM 不可见 | "问谁呀" | `render_message_text` 渲染 @ 为 "@昵称" |
| 句中 @ 昵称 | 同上 | 同上（勿回退 extract_plain_text） |
| 探针踩 5s 限频 | 探针偶发失败 | 探针间隔 ≥6s |
| bigram 碎片与真黑话同分 | "来张/张表"淹没候选 | 常用字过滤 + PMI 提纯 |
| `.format` 撞 JSON 花括号 | KeyError '"kind"' | 规则文案占位用 `.replace("{name}")` |
| `start-bot.cmd` 编码 | UTF-8 中文把脚本拆坏、bot 静默不启动 | 保持 GBK + CRLF |
| bot/ 根目录手建 context.db | 0 字节垃圾库被 git 收编 | 真库在 `bot/data/`；已补 .gitignore |

### 9.4 借鉴 astrbot self-learning 的清单（全部已落地）

| 借鉴点 | 落地 | 提交 |
|---|---|---|
| 注入保 prefix caching | `dynamic_blocks` 拼用户消息尾部 | b91d1ca |
| 15 天二次衰减 | `core/decay.py` 共享曲线（贴图/风格共用） | b91d1ca |
| few-shot 风格学习（机械提取） | `core/style_pairs.py` + style_loop | fef4a57 |
| 黑话统计预筛 + LLM 推断 | `core/jargon.py` + jargon_loop（升级为四信号+PMI） | fef4a57 |
| 好感度守恒（双上限+再分配） | `core/affection.py` | 7f37e13 |
| 人格演化审查（pending→批准→回滚） | `core/persona_evo.py` | 7f37e13 |
| "只理解不复读"注入纪律 | jargon.for_prompt 原文 | fef4a57 |
| 未采纳：社交图谱/知识图谱/WebUI/目标系统 | 评估后判定低性价比（见研究文档） | — |

---

## 10. 技术指标

| 指标 | 数值 |
|---|---|
| 核心代码 | core 3,074 行 + plugins 1,221 行（不含测试） |
| 测试 | 112 单测（~3s）+ 14 探针 + e2e 全链路 |
| 数据表 | 12 张（SQLite WAL） |
| 配置项 | 55 个（.env，分 9 组） |
| 命令 | 13 个（6 个管理员专属） |
| 后台循环 | 5 个（记忆/情绪/风格/黑话/人格演化） |
| LLM 调用预算 | 单次 10w / 单日 1kw tokens，记账持久化 |
| 响应延迟 | 普通回复 ~5-15s（含思考）；带搜索 ~30-60s |

---

*本文档随代码演进维护；每层功能的深挖细节在各模块的 docstring 与 skill 文档（qqbot-napcat-nonebot）中。*
