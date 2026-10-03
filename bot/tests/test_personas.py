import pytest

from core import context, personas
from core.config import load_config


def _mk(base, stem: str, title: str, body: str) -> None:
    (base / f"{stem}.md").write_text(f"# 人设：{title}\n\n{body}", encoding="utf-8")


@pytest.fixture()
def pdir(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "_BASE", tmp_path)
    personas._cache.clear()
    return tmp_path


def test_list_and_match(pdir):
    _mk(pdir, "persona", "十九", "默认人设")
    _mk(pdir, "persona-fox", "Fox", "狐狸人设")
    names = {p["name"] for p in personas.list_personas()}
    assert names == {"十九", "Fox"}
    assert personas.match("十九")["stem"] == "persona"
    assert personas.match("fox")["stem"] == "persona-fox"
    assert personas.match("FOX")["stem"] == "persona-fox"
    assert personas.match("persona-fox")["stem"] == "persona-fox"
    assert personas.match("不存在") is None


def test_group_switch(pdir, monkeypatch):
    context.reset(pdir / "ctx.db")
    _mk(pdir, "persona", "十九", "默认人设")
    _mk(pdir, "persona-fox", "Fox", "狐狸人设")
    monkeypatch.setattr(personas, "get_config", lambda: load_config({"LLM_SYSTEM_PROMPT": "# 人设：十九\n默认"}))
    # 默认
    name, _ = personas.resolve(111)
    assert name == "十九"
    # 切换
    personas.set_group(111, "persona-fox")
    assert personas.get_group_stem(111) == "persona-fox"
    name2, text2 = personas.resolve(111)
    assert name2 == "Fox" and "狐狸人设" in text2
    assert personas.name_for(111) == "Fox"
    # 恢复默认
    personas.clear_group(111)
    name3, _ = personas.resolve(111)
    assert name3 == "十九"
    # 文件被删后回退默认
    personas.set_group(111, "persona-fox")
    (pdir / "persona-fox.md").unlink()
    personas._cache.clear()
    name4, _ = personas.resolve(111)
    assert name4 == "十九"
