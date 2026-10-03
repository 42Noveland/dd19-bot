from core.config import load_config


def test_parse_group_ids_supports_comma_and_fullwidth():
    cfg = load_config({"ALLOWED_GROUP_IDS": "123, 456，789", "LLM_ENABLED": "1"})
    assert cfg.allowed_group_ids == {123, 456, 789}
    assert cfg.llm_enabled is True


def test_empty_group_ids_means_nobody_allowed():
    cfg = load_config({})
    assert cfg.allowed_group_ids == set()
    assert cfg.llm_enabled is False


def test_bad_values_ignored():
    cfg = load_config({"ALLOWED_GROUP_IDS": "abc, 42, ,x9"})
    assert cfg.allowed_group_ids == {42}


def test_providers_built_with_key_fallbacks():
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok"})
    assert cfg.providers["local"].base_url == "http://127.0.0.1:8080/v1"
    assert cfg.providers["opencode_go"].base_url == "https://opencode.ai/zen/go/v1"
    assert cfg.providers["opencode_go"].model == "deepseek-v4.1-flash"
    assert cfg.providers["opencode_go"].api_key == "ok"
    assert "deepseek" not in cfg.providers  # deepseek 后端已移除


def test_thinking_defaults_on_for_api_providers():
    # API 提供商默认开思考、默认档位 high（用户要求：保质量）
    cfg = load_config({})
    assert cfg.providers["opencode_go"].extra_payload == {"reasoning_effort": "high"}


def test_thinking_toggle_and_effort_normalization():
    cfg = load_config(
        {
            "LLM_OPENCODE_GO_THINKING": "off",
            "LLM_OPENCODE_GO_REASONING_EFFORT": "xhigh",  # xhigh → max
        }
    )
    assert cfg.providers["opencode_go"].extra_payload == {"thinking": {"type": "disabled"}}
    # 非法档位回落到默认 high
    bad = load_config({"LLM_OPENCODE_GO_REASONING_EFFORT": "bogus"})
    assert bad.providers["opencode_go"].extra_payload["reasoning_effort"] == "high"


def test_reply_mode_parse():
    # 默认=mention：@机器人即聊（不 @ 不聊）；all=所有非命令消息；command=仅 /chat
    assert load_config({}).llm_reply_mode == "mention"
    assert load_config({"LLM_REPLY_MODE": "mention"}).llm_reply_mode == "mention"
    assert load_config({"LLM_REPLY_MODE": "COMMAND"}).llm_reply_mode == "command"
    assert load_config({"LLM_REPLY_MODE": "all"}).llm_reply_mode == "all"
    assert load_config({"LLM_REPLY_MODE": "bogus"}).llm_reply_mode == "mention"


def test_persona_file_loading(tmp_path):
    f = tmp_path / "persona.md"
    f.write_text("你是测试人设", encoding="utf-8")
    cfg = load_config({"LLM_PERSONA_FILE": str(f)})
    assert cfg.llm_system_prompt == "你是测试人设"
    # LLM_SYSTEM_PROMPT 直写优先于人设文件
    cfg2 = load_config({"LLM_PERSONA_FILE": str(f), "LLM_SYSTEM_PROMPT": "覆盖"})
    assert cfg2.llm_system_prompt == "覆盖"
    # 文件不存在 → 无人设（不报错）
    cfg3 = load_config({"LLM_PERSONA_FILE": str(tmp_path / "nope.md")})
    assert cfg3.llm_system_prompt == ""


def test_budget_and_tools_config_parse():
    cfg = load_config({})
    assert cfg.llm_daily_token_limit == 10_000_000
    assert cfg.llm_quota_reply == "白饭吃完了QAQ"
    assert cfg.llm_tool_max_rounds == 3
    assert cfg.providers["opencode_go"].supports_tools is True
    assert cfg.providers["local"].supports_tools is False
    cfg2 = load_config(
        {
            "LLM_DAILY_TOKEN_LIMIT": "123",
            "LLM_QUOTA_REPLY": "没了",
            "LLM_TOOL_MAX_ROUNDS": "5",
            "LLM_LOCAL_TOOLS": "on",
            "SEARCH_ENABLED": "0",
            "SEARCH_API_KEY": "sk",
        }
    )
    assert cfg2.llm_daily_token_limit == 123
    assert cfg2.llm_quota_reply == "没了"
    assert cfg2.llm_tool_max_rounds == 5
    assert cfg2.providers["local"].supports_tools is True
    assert cfg2.search_enabled is False
    assert cfg2.search_api_key == "sk"


def test_provider_alias_and_fallbacks():
    cfg = load_config(
        {"LLM_PROVIDER": "opencode-go", "LLM_FALLBACKS": "local, 本地 ,local, opencode-go"}
    )
    assert cfg.llm_provider == "opencode_go"
    assert cfg.llm_fallbacks == ["local"]  # 未知名丢弃、去重、剔除主选
    # 已移除的 deepseek 不再是合法后端：显式指定时回落到 local
    cfg2 = load_config({"LLM_PROVIDER": "deepseek"})
    assert cfg2.llm_provider == "local"


def test_context_and_vision_config_parse():
    cfg = load_config({})
    assert cfg.llm_context_messages == 12
    assert cfg.vision_enabled is True
    assert cfg.vision_model == "deepseek-v4-flash-vision-exp"
    cfg2 = load_config({"LLM_CONTEXT_MESSAGES": "0", "VISION_ENABLED": "off", "VISION_MODEL": "x-vision"})
    assert cfg2.llm_context_messages == 0
    assert cfg2.vision_enabled is False
    assert cfg2.vision_model == "x-vision"


def test_sticker_config_parse():
    assert load_config({}).sticker_enabled is True
    assert load_config({"STICKER_ENABLED": "0"}).sticker_enabled is False


def test_quote_reply_config_parse():
    assert load_config({}).quote_reply_enabled is True
    assert load_config({"QUOTE_REPLY_ENABLED": "0"}).quote_reply_enabled is False


def test_auto_reply_config_parse():
    cfg = load_config({})
    assert cfg.auto_reply_enabled is True
    assert abs(cfg.auto_reply_chance - 0.35) < 1e-9
    assert cfg.auto_reply_cooldown == 240
    cfg2 = load_config({"AUTO_REPLY_ENABLED": "0", "AUTO_REPLY_CHANCE": "0.5", "AUTO_REPLY_COOLDOWN": "60"})
    assert cfg2.auto_reply_enabled is False and cfg2.auto_reply_cooldown == 60


def test_memory_config_parse():
    cfg = load_config({})
    assert cfg.memory_enabled is True and cfg.memory_batch == 30 and cfg.memory_tick == 120
    cfg2 = load_config({"MEMORY_ENABLED": "0", "MEMORY_BATCH": "50", "MEMORY_TICK": "60"})
    assert cfg2.memory_enabled is False and cfg2.memory_batch == 50 and cfg2.memory_tick == 60
