"""Промпт сборщика уходит в родном шаблоне загруженной модели, содержимое — то же."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote_worker"))
from models.mlx_backend import native_prompt  # noqa: E402

CHATML = ("<|im_start|>system\nSYS<|im_end|>\n<|im_start|>user\nRULES and CONTEXT\n1. Hello"
          "<|im_end|>\n<|im_start|>assistant\n</think>\n\n")


class _Tok:
    def __init__(self, tmpl, system_ok=True):
        self.chat_template, self.system_ok, self.seen = tmpl, system_ok, None

    def apply_chat_template(self, messages, **kw):
        if not self.system_ok and messages[0]["role"] == "system":
            raise ValueError("no system role")
        self.seen = messages
        return "<B>" + "|".join(m["content"] for m in messages) + "<A>"


def test_qwen_prompt_is_untouched():
    assert native_prompt(_Tok("...<|im_start|>..."), CHATML) == CHATML


def test_other_model_gets_its_own_template_with_the_same_content():
    t = _Tok("{{ hunyuan }}")
    out = native_prompt(t, CHATML)
    assert out == "<B>SYS|RULES and CONTEXT\n1. Hello<A>"
    assert [m["role"] for m in t.seen] == ["system", "user"]


def test_template_without_system_role_gets_it_merged_into_user():
    t = _Tok("{{ gemma }}", system_ok=False)
    assert native_prompt(t, CHATML) == "<B>SYS\n\nRULES and CONTEXT\n1. Hello<A>"
