# 朋友群 QQ 机器人（NapCat Node 版 + NoneBot2）

小号 **123456789**（群昵称 dd19；人设名 **十九**，普通朋友风格）在本机 Windows 常驻，
接入三个白名单群；LLM 支持双后端（opencode-go 中转 / 本机 llama.cpp）。

- 测试群：111111111 ｜ 朋友群：222222222 ｜ 新群：333333333
- 管理员（/model 指令）：1234567890
- 机器人：123456789（昵称 dd19）

## 目录结构

```
D:\agent-workspace\qqbot\
├─ bot\                      NoneBot2 工程（核心/插件/测试/人设）
│   ├─ bot.py                入口（OneBot v11 适配器 + 插件加载）
│   ├─ core\                 核心（config/llm/budget/search/gate/context/vision）
│   ├─ plugins\              插件（basic/llm_chat/observer 消息观察者）
│   ├─ persona.md            人设「十九」（system prompt，可编辑）
│   ├─ data\                 感知层数据（context.db 群上下文 / images 图片库，gitignore）
│   ├─ start-bot.cmd         一键启动脚本
│   └─ tests\e2e\            全链路自测（fake_napcat + 探针×2）
├─ napcat\NapCat.Shell.Node\ NapCat 协议端（Node 版，自带 QQ 纯 shell 核心）
└─ napcat\downloads\         组件压缩包备份（NapCat.Shell.Windows.Node.zip）
```

## 启动顺序

1. **NapCat**：双击 `napcat\NapCat.Shell.Node\napcat.bat`（等价 `node.exe ./index.js`），
   等日志出现 `Worker进程已登录成功`（一般自动快速登录，无需扫码）。
2. **bot**：双击 `bot\start-bot.cmd`（保持窗口开着；若提示端口 8081 已被占用，
   按任意键 = 结束旧实例并一键重启）。
   日志出现 `Bot 123456789 connected` 即接通（日志同时按天存档到 `bot\logs\bot-YYYY-MM-DD.log`）。
3. （可选）**本机模型**：`D:\agent-workspace\llmtest\start-qwen38.cmd`（:8080）；
   启动后 `/model local` 切到本机模型（免费、数据不出本机）。

## 自检命令

| 项目 | 命令/位置 | 期望 |
|---|---|---|
| 端口存活 | 浏览器开 http://127.0.0.1:8081/ | 返回 404 = 正常 |
| 单元测试 | `cd bot && .venv\Scripts\python.exe -m pytest -q tests` | `79 passed` |
| 感知层探针 | bot 运行时 `.venv\Scripts\python.exe tests\e2e\probe_context_vision.py [图片URL]` | 记忆/图片均 ✓ |
| @ 解析探针 | `.venv\Scripts\python.exe tests\e2e\probe_mention_parse.py` | 4 种 @ 形态正常 |
| 表情包探针 | `.venv\Scripts\python.exe tests\e2e\probe_sticker.py` | 收到图片发送 |
| 自然配图探针 | `.venv\Scripts\python.exe tests\e2e\probe_sticker_natural.py [消息]` | 情绪语境下模型主动配图（统计性） |
| 引用探针 | `.venv\Scripts\python.exe tests\e2e\probe_quote_reply.py` | 回复带 reply 段且 id 匹配 |
| 主动接话探针 | `.venv\Scripts\python.exe tests\e2e\probe_auto_reply.py [消息]` | 判定→接话→引用回复（统计性，带冷却） |
| 记忆探针 | `.venv\Scripts\python.exe tests\e2e\probe_memory.py` | 提炼+注入（回答含"蓝色"） |
| 全链路自测 | bot 运行时 `bot\.venv\Scripts\python.exe bot\tests\e2e\fake_napcat.py` | `ALL PASS` |
| NapCat 面板 | http://127.0.0.1:6099/webui（token 见 `napcat\NapCat.Shell.Node\napcat\config\webui.json`） | 仅本机可访问 |
- NapCat HTTP API：127.0.0.1:3000（仅本机，调试/运维用；get_image、send_group_msg 等）
| 群内 | `/ping` `/jrrp` `/help`；`@dd19 内容`；`/search 关键词`；`/usage`；`/model`（管理员） | 正常回复 |

## 关键配置（bot\.env）

| 键 | 当前值 | 说明 |
|---|---|---|
| ALLOWED_GROUP_IDS | 111111111,222222222,333333333 | 白名单群（留空=全不响应） |
| SUPERUSERS | ["1234567890"] | /model 管理员 |
| LLM_ENABLED | 1 | 聊天总开关 |
| LLM_PROVIDER | opencode_go | 主选后端（deepseek-v4.1-flash） |
| LLM_FALLBACKS | （空） | 回退链；留空=只用主选。可填 local 让本机模型兜底 |
| LLM_REPLY_MODE | mention | 默认：@我/引用回复才聊；all=所有消息都聊；command=仅 /chat |
| LLM_PERSONA_FILE | persona.md | 人设文件；也可用 LLM_SYSTEM_PROMPT 单行直写（优先级更高） |
| LLM_MAX_TOKENS | 100000 | 单次会话 token 上限（输入估算+输出上限合计；超长输入自动截断） |
| LLM_DAILY_TOKEN_LIMIT | 10000000 | 单日 token 上限（跨后端合计；北京时间每日重置，存 bot/logs/token-usage.json） |
| LLM_QUOTA_REPLY | 白饭吃完了QAQ | 额度用完后的固定回复 |
| LLM_TOOL_MAX_ROUNDS | 3 | web_search 工具调用的最大轮次 |
| SEARCH_ENABLED / SEARCH_API_KEY | 1 / Firecrawl | 联网搜索（LLM 按需调用 web_search；/search 手动触发） |
| LLM_CONTEXT_MESSAGES | 12 | 回复时附带的最近群聊条数（0=关闭上下文注入） |
| VISION_ENABLED / VISION_MODEL | 1 / deepseek-v4-flash-vision-exp | 图片识别（云 vision 主，本地 MiniCPM-V 兜底） |
| STICKER_ENABLED | 1 | 表达层：LLM 可调 send_sticker 发表情包（图库自动从群图片收集） |
| QUOTE_REPLY_ENABLED | 1 | 表达层：聊天回复引用触发消息（命令类回复不引用） |
| AUTO_REPLY_ENABLED | 1 | 决策层：不@也会判断着接话 |
| AUTO_REPLY_CHANCE / AUTO_REPLY_COOLDOWN | 0.35 / 240 | 接话概率门 / 每群冷却秒数 |
| MEMORY_ENABLED / MEMORY_BATCH / MEMORY_TICK | 1 / 30 / 120 | 记忆系统：总开关 / 每批消息数 / 检查间隔秒 |
| LLM_*_TOOLS | opencode_go=on, local=off | 各后端是否启用 function calling |
| LLM_TIMEOUT | 180 | 单次请求超时（秒） |
| LLM_COOLDOWN | 5 | 每人每群限频（秒） |
| LLM_OPENCODE_GO_* | 思考=on，档位 medium | API 后端的思考参数 |

## 行为说明

- **触发**：@dd19（真实 @ 或文字形式"@dd19"均可）或引用回复机器人消息 → 聊天（人设「十九」）；
  命令（/ping 等）直接发即可，无需 @；非白名单群完全静默。
- **联网搜索**：LLM 按需调用 `web_search` 工具（天气/新闻/价格/事实核查类问题会自动搜，
  回复附参考链接）；也可手动 `/search 关键词`。搜索走 Firecrawl API。
- **群上下文（感知层）**：机器人**静默记录**白名单群的全部消息（本地 SQLite `bot/data/context.db`，保留 7 天）；
  回复时自动附带最近 12 条群聊作为背景（`LLM_CONTEXT_MESSAGES` 可调，0=关闭）——它"看得到"群里在聊什么，
  也记得住刚才谁说过什么。
- **图片识别（感知层）**：群里的图片即收即下——优先从 NapCat 本地缓存取图（QQ CDN 链接短时效，不可依赖）、md5 去重后走 opencode-go 云 vision
  生成描述（本地 MiniCPM-V 兜底），描述写回上下文——重复的图零成本；识别结果同时是贴图库的底账。
  `VISION_ENABLED=0` 可关闭。
- **表情包回应（表达层）**：LLM 回复时会**自然配图**——觉得配一张更带感（吐槽/接梗/情绪/安慰）就主动调用
  `send_sticker` 从图库挑一张发出，不用等对方要图（系统提示引导 + 同义情绪词扩展 + 频率自控：约 3~5 条回复最多 1 张）；
  明确要图/要重发时必配。图库自动从群里出现过的图片积累（md5 去重），同群 10 分钟内不重复发同一张。
  `STICKER_ENABLED=0` 可关闭。
- **引用回复（表达层）**：聊天回复会自动**引用**触发它的那条消息（一眼看清在回谁）；命令类回复（/ping、/search 等）
  保持干净不引用。`QUOTE_REPLY_ENABLED=0` 可关闭。
- **主动接话（决策层）**：没人 @ 的时候它也会**判断着接话**——每条群消息先过廉价预筛（每群冷却、刚说过话不抢话），
  再按概率门抽样（提到它名字的消息必判），由 LLM 判断"该不该接"（宁缺毋滥：拿不准就不接）；
  接话复用聊天流程（带引用、可配表情包）。`AUTO_REPLY_ENABLED=0` 可关；
  `AUTO_REPLY_CHANCE`（默认 0.35）与 `AUTO_REPLY_COOLDOWN`（默认 240s）控制活跃度。
- **长期记忆（记忆系统）**：后台每 2 分钟检查一次，把群聊里值得记住的事批量提炼成"成员事实 + 群事件"
  （增量处理、去重、上限淘汰），回复时自动注入相关记忆——**跨天记得住人**（不受 7 天消息滚动与 12 条上下文窗口限制）。
  `/memory`（管理员）查看统计；`/memory extract` 立即提炼。`MEMORY_ENABLED=0` 可关。
- **限频**：同一人同一群 5 秒内只能触发一次聊天/搜索。
- **回退链**：主选失败自动尝试下一个后端，回复末尾可用 `LLM_SHOW_PROVIDER=1` 显示 `[via xxx]`。
- **Token 控制**：每次 @ 是单轮请求（附最近群聊背景，见上；工具调用各轮也计入预算）。
  单次会话上限 `LLM_MAX_TOKENS`（10w，输入估算+输出上限合计；超长输入自动截断）；
  单日上限 `LLM_DAILY_TOKEN_LIMIT`（1kw，北京时间每日重置、持久化、重启不丢）；
  **额度用完时 @bot 只会回复「白饭吃完了QAQ」**；`/usage` 可查当日用量与剩余。
  输出被截断时会附提示；思考占满预算导致空正文会自动回退下一后端。

## 故障排查

| 现象 | 处理 |
|---|---|
| 群里无响应 | ①bot 窗口在跑吗 ②NapCat WebUI 连接状态 ③群号在白名单吗 |
| @ 没反应 | 看 bot 日志有没有收到事件；文字@兼容层支持"@昵称/@QQ号"前缀 |
| /chat 报错或很慢 | `/model test <后端>` 逐个测：local 看 llama(:8080)；API 看 key/网络 |
| NapCat 掉线 | 重新 napcat.bat；频繁掉线参考"风险提示" |
| WebUI 打不开 | 已限制仅本机（127.0.0.1）；token 在 webui.json |

## 风险提示

- 第三方协议端（NapCat）存在 QQ 账号风控风险：仅用小号、低频、白名单运行；不对外暴露端口。
- API 后端消耗：opencode_go 为订阅额度（三窗口限额）；回退链当前为空（可用 local 兜底）。
- 人设与聊天内容：API 后端会把内容发给对应服务方；本机模型不外发。

## 备份

`bot\.env`、`bot\persona.md`、`napcat\NapCat.Shell.Node\napcat\config\webui.json`、
`...\config\onebot11_123456789.json` → 复制到 `D:\agent-workspace\qqbot-backup\`（改配置后手动同步）。

## 实施备注（2026-10-02）

- OneKey 一键包因腾讯 CDN 旧链接失效（404，见 NapCat issue #1973）不可用 →
  改用 **NapCat.Shell.Windows.Node.zip**（自带 QQ 纯 shell 核心 9.9.31-49738，免装 QQ 客户端）。
- Node 包缺 wrapper.node 的两个静态依赖 `crypto.dll` / `ssl.dll`，
  已从本机 QQ 安装目录（`D:\tools\qq\versions\9.9.31-49738\resources\app\`）复制补齐。
- NapCat 账号数据目录：`C:\Users\Noveland\Documents\Tencent Files\NapCat\data`。
- 端口：6099（WebUI，仅本机）/ 8081（bot 反向 WS）/ 8080（本机 llama，可选）。
- 不装系统服务、不改 PATH/注册表；所有文件在 `D:\agent-workspace\qqbot\` 内。

## 验收记录（2026-10-02）

- 单元测试：**44 passed**（配置解析/限频/回退链/思考参数/人设注入/文字@兼容/白名单/
  工具循环/预算记账/搜索解析/配额拦截）。
- 协议级 e2e（fake_napcat）：**ALL PASS** —— /ping、/jrrp、非白名单静默、
  mention 路由（普通消息静默）、真实 @ 聊天、**文字@命令 + 文字@聊天**、
  **/search 指令**、**LLM 按需调用 web_search 工具**、/model 列表/切换/还原。
- 实机验证：
  - 测试群与朋友群：`/ping` → `pong!`（含"文字@"形式）✔
  - `@dd19 一句话介绍你自己` → AI 回复 ✔
  - 默认模型 **opencode_go / deepseek-v4.1-flash**（思考 medium）；链路实测 ✔
  - **人设「十九」**（普通朋友风格，参考百度智能云《人设prompt撰写最佳实践》"林晚"示例等
    公开模板改写）替换猫娘并实测 ✔（自然口语、无卖萌、身份如实）
  - **联网搜索**：@dd19 "帮我搜一下北京今天的天气" → 自动搜索并作答（附来源）✔；
    `/search 北京今天天气` → 自然口语总结 + 参考链接 ✔
  - **Token 预算**：单次会话上限 10w、单日上限 1kw 生效；记账持久化于
    `bot/logs/token-usage.json`（重启不丢、按北京时间跨天重置）；超限固定回复「白饭吃完了QAQ」✔
- **工具调用修复（2026-10-03）**：一轮内多个 tool_call 逐个回填 + 末轮强制收尾（逼出最终回答），
  修复"多搜索请求 400 / 轮次超限"；DeepSeek 后端移除（链=opencode_go；local 保留可切换）✔
- **感知层上线（2026-10-03）**：群上下文记录+注入（实测："我最喜欢的数字是73"→被问时答"73 啊，你刚说的"）✔；
  图片识别全链路（下载→去重→云 vision→回填：测试图读出"苹果数量=42"；真实群表情包识别+回填）✔；
  句中 @ 昵称渲染、回复自身记录入上下文 ✔；单测 56 passed。
- **表达层·表情包回应（2026-10-03）**：send_sticker 工具上线（LLM 按需调用→图库语义匹配→发图→记账）；
  实测探针："来张'得意'的表情包" → 发出群里收集的女仆图 + "发了，够得意了吧哈哈" ✔；单测 79 passed。
- **图片管道加固（2026-10-03）**：取图改为 **NapCat 本地缓存优先**（`get_image` API），修掉 CDN 链接过期导致的静默失败；
  补识别 2 张历史图（含 1.3MB 动图）；真机发图实测通过（`file:///` 路径，retcode 0）。
- **自然配图（2026-10-03）**：回复时按对话情绪主动配图（系统提示 + 同义扩展 + 频率自控）；
  实测"今天也太无语了…裂开了" → 主动发出"被生活拿捏"虎斑猫图 + "先给你配个图…" ✔；单测 79 passed。
- **引用回复（表达层，2026-10-03）**：聊天回复带引用段（引用触发消息）；命令回复不引用；探针 + 真机实测 ✓；单测 79 passed。
- **决策层·主动接话（2026-10-03）**：不@也接话（预筛+概率门+LLM 判断+静默复用聊天流程）；
  实测"dd19 在吗，出来冒个泡" → 判定"接"→"在的在的，冒泡了🫧 有啥事儿你说"（带引用）✔；冷却防刷 ✔；单测 79 passed。
- **记忆系统（2026-10-03）**：长期记忆提炼（增量/去重/上限）+ 回复自动注入 + 后台循环 + /memory 命令；
  实测：事实挤出上下文窗口后仍被记住并答出（"蓝色"）✔；单测 79 passed。
- 未做/待办：本机 llama（local 后端）实机测试（需先启动 start-qwen38.cmd 后 `/model test local`）；
  手机访问 6099 的负测试（WebUI 已限 127.0.0.1）；48 小时风控观察。