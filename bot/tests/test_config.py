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
    cfg = load_config({"DEEPSEEK_API_KEY": "dk", "OPENCODE_GO_API_KEY": "ok"})
    assert cfg.providers["local"].base_url == "http://127.0.0.1:8080/v1"
    assert cfg.providers["deepseek"].base_url == "https://api.deepseek.com/v1"
    assert cfg.providers["deepseek"].model == "deepseek-flash"
    assert cfg.providers["deepseek"].api_key == "dk"
    assert cfg.providers["opencode_go"].base_url == "https://opencode.ai/zen/go/v1"
    assert cfg.providers["opencode_go"].model == "deepseek-v4.1-flash"
    assert cfg.providers["opencode_go"].api_key == "ok"


def test_thinking_defaults_on_for_api_providers():
    # 两个 API 提供商默认开思考、默认档位 high（用户要求：保质量）
    cfg = load_config({})
    assert cfg.providers["deepseek"].extra_payload == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    assert cfg.providers["opencode_go"].extra_payload == {"reasoning_effort": "high"}


def test_thinking_toggle_and_effort_normalization():
    cfg = load_config(
        {
            "LLM_DEEPSEEK_THINKING": "off",
            "LLM_OPENCODE_GO_REASONING_EFFORT": "xhigh",  # xhigh → max
        }
    )
    assert cfg.providers["deepseek"].extra_payload == {"thinking": {"type": "disabled"}}
    assert cfg.providers["opencode_go"].extra_payload == {"reasoning_effort": "max"}
    # 非法档位回落到默认 high
    bad = load_config({"LLM_DEEPSEEK_REASONING_EFFORT": "bogus"})
    assert bad.providers["deepseek"].extra_payload["reasoning_effort"] == "high"


def test_reply_mode_parse():
    # 默认=mention：@机器人即聊（不 @ 不聊）；all=所有非命令消息；command=仅 /chat
    assert load_config({}).llm_reply_mode == "mention"
    assert load_config({"LLM_REPLY_MODE": "mention"}).llm_reply_mode == "mention"
    assert load_config({"LLM_REPLY_MODE": "COMMAND"}).llm_reply_mode == "command"
    assert load_config({"LLM_REPLY_MODE": "all"}).llm_reply_mode == "all"
    assert load_config({"LLM_REPLY_MODE": "bogus"}).llm_reply_mode == "mention"


def test_persona_file_loading(tmp_path):
    f = tmp_path / "persona.md"
    f.write_text("你是 Nova", encoding="utf-8")
    cfg = load_config({"LLM_PERSONA_FILE": str(f)})
    assert cfg.llm_system_prompt == "你是 Nova"
    # LLM_SYSTEM_PROMPT 直写优先于人设文件
    cfg2 = load_config({"LLM_PERSONA_FILE": str(f), "LLM_SYSTEM_PROMPT": "覆盖"})
    assert cfg2.llm_system_prompt == "覆盖"
    # 文件不存在 → 无人设（不报错）
    cfg3 = load_config({"LLM_PERSONA_FILE": str(tmp_path / "nope.md")})
    assert cfg3.llm_system_prompt == ""


def test_provider_alias_and_fallbacks():
    cfg = load_config(
        {"LLM_PROVIDER": "opencode-go", "LLM_FALLBACKS": "deepseek, 本地 ,deepseek, opencode-go"}
    )
    assert cfg.llm_provider == "opencode_go"
    assert cfg.llm_fallbacks == ["deepseek"]  # 未知名丢弃、去重、剔除主选
