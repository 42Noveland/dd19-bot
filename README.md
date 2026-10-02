# 朋友群 QQ 机器人（NapCat Node 版 + NoneBot2）

小号 **123456789**（群昵称 dd19；人设名 **Nova**，猫娘）在本机 Windows 常驻，
接入两个白名单群；LLM 支持三后端（本机 llama.cpp / DeepSeek 官方 / opencode-go 中转）。

- 测试群：111111111 ｜ 朋友群：222222222
- 管理员（/model 指令）：1234567890
- 机器人：123456789（昵称 dd19）

## 目录结构

```
D:\agent-workspace\qqbot\
├─ bot\                      NoneBot2 工程（核心/插件/测试/人设）
│   ├─ bot.py                入口（OneBot v11 适配器 + 插件加载）
│   ├─ persona.md            猫娘 Nova 人设（system prompt，可编辑）
│   ├─ start-bot.cmd         一键启动脚本
│   └─ tests\e2e\fake_napcat.py  全链路自测脚本
├─ napcat\NapCat.Shell.Node\ NapCat 协议端（Node 版，自带 QQ 纯 shell 核心）
└─ napcat\downloads\         组件压缩包备份（NapCat.Shell.Windows.Node.zip）
```

## 启动顺序

1. **NapCat**：双击 `napcat\NapCat.Shell.Node\napcat.bat`（等价 `node.exe ./index.js`），
   等日志出现 `Worker进程已登录成功`（一般自动快速登录，无需扫码）。
2. **bot**：双击 `bot\start-bot.cmd`（保持窗口开着，CTRL+C 停止）。
   日志出现 `Bot 123456789 connected` 即接通。
3. （可选）**本机模型**：`D:\agent-workspace\llmtest\start-qwen38.cmd`（:8080）；
   启动后 `/model local` 切到本机模型（免费、数据不出本机）。

## 自检命令

| 项目 | 命令/位置 | 期望 |
|---|---|---|
| 端口存活 | 浏览器开 http://127.0.0.1:8081/ | 返回 404 = 正常 |
| 单元测试 | `cd bot && .venv\Scripts\python.exe -m pytest -q tests` | `25 passed` |
| 全链路自测 | bot 运行时 `bot\.venv\Scripts\python.exe bot\tests\e2e\fake_napcat.py` | `ALL PASS` |
| NapCat 面板 | http://127.0.0.1:6099/webui（token 见 `napcat\NapCat.Shell.Node\napcat\config\webui.json`） | 仅本机可访问 |
| 群内 | `/ping` `/jrrp` `/help`；`@dd19 内容`；`/model`（管理员） | 正常回复 |

## 关键配置（bot\.env）

| 键 | 当前值 | 说明 |
|---|---|---|
| ALLOWED_GROUP_IDS | 111111111,222222222 | 白名单群（留空=全不响应） |
| SUPERUSERS | ["1234567890"] | /model 管理员 |
| LLM_ENABLED | 1 | 聊天总开关 |
| LLM_PROVIDER | opencode_go | 主选后端（deepseek-v4.1-flash） |
| LLM_FALLBACKS | deepseek,opencode_go | 回退链（实际生效：deepseek；不想耗额度可清空） |
| LLM_REPLY_MODE | mention | 默认：@我/引用回复才聊；all=所有消息都聊；command=仅 /chat |
| LLM_PERSONA_FILE | persona.md | 人设文件；也可用 LLM_SYSTEM_PROMPT 单行直写（优先级更高） |
| LLM_MAX_TOKENS | 2000 | 单次生成输出上限（思考+正文的总预算） |
| LLM_TIMEOUT | 180 | 单次请求超时（秒） |
| LLM_COOLDOWN | 5 | 每人每群限频（秒） |
| LLM_DEEPSEEK_* / LLM_OPENCODE_GO_* | 思考=on，档位 medium | 两个 API 后端的思考参数 |

## 行为说明

- **触发**：@dd19（真实 @ 或文字形式"@dd19"均可）或引用回复机器人消息 → 猫娘聊天；
  命令（/ping 等）直接发即可，无需 @；非白名单群完全静默。
- **限频**：同一人同一群 5 秒内只能触发一次聊天。
- **回退链**：主选失败自动尝试下一个后端，回复末尾可用 `LLM_SHOW_PROVIDER=1` 显示 `[via xxx]`。
- **Token 控制**：本项目**不做多轮上下文**——每次 @ 都是独立单轮请求
  （`messages = [system 人设 + 这一条消息]`），不存在会话越聊越长的问题；
  输出上限由 `LLM_MAX_TOKENS`（请求里的 `max_tokens`）控制；
  `finish_reason=length` 时回复会附"被截断"提示；思考占满预算导致空正文会自动回退下一后端。
  输入侧无额外截断（模型自身上下文窗口兜底，本机 llama 配置 65536）。

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
- API 后端消耗：opencode_go 为订阅额度（三窗口限额）、deepseek 按量计费；
  回退链会静默消耗额度——不想花钱把 `LLM_FALLBACKS` 清空。
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

- 单元测试：**25 passed**（配置解析/限频/回退链/思考参数/人设注入/文字@兼容/白名单）。
- 协议级 e2e（fake_napcat）：**ALL PASS** —— /ping、/jrrp、非白名单静默、
  mention 路由（普通消息静默）、真实 @ 聊天、**文字@命令 + 文字@聊天**、/model 列表/切换/还原。
- 实机验证：
  - 测试群与朋友群：`/ping` → `pong!`（含"文字@"形式）✔
  - `@dd19 一句话介绍你自己` → AI 回复（deepseek 时期）✔
  - 默认模型切换为 **opencode_go / deepseek-v4.1-flash**，两个 API 思考档位 **medium**；链路实测 ✔
  - **猫娘 Nova 人设**注入（persona.md）并实测 ✔（"我是dd19呀，群里的猫娘小助手喵~"→ 后升级为完整 SOUL）
- 未做/待办：本机 llama（local 后端）实机测试（需先启动 start-qwen38.cmd 后 `/model test local`）；
  手机访问 6099 的负测试（WebUI 已限 127.0.0.1）；48 小时风控观察。
