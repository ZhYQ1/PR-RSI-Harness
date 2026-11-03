import pytest

from src.feedback.generator import GUIDELINES, CandidateGenerator


class StubLLM:
    def __init__(self, reply: str = "NEW PROMPT", fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.messages: list[list[dict]] = []

    def chat(self, messages):
        self.messages.append(messages)
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.reply


@pytest.fixture(autouse=True)
def _no_spark_key(monkeypatch):
    monkeypatch.delenv("SPARK_API_KEY", raising=False)


def test_template_fallback_is_deterministic():
    generator = CandidateGenerator(llm=None)
    samples = [{"case_type": "false_negative", "predicted": [], "expected": ["sql_injection"]}]
    kind1, content1 = generator.generate("missed:sql_injection", samples)
    kind2, content2 = generator.generate("missed:sql_injection", samples)
    assert kind1 == kind2 == "prompt"
    assert content1 == content2
    assert GUIDELINES["sql_injection"] in content1
    assert 'cluster "missed:sql_injection"' in content1


def test_unknown_cluster_gets_generic_check():
    generator = CandidateGenerator(llm=None)
    _, content = generator.generate("weird_pattern", [])
    assert "weird_pattern" in content
    assert "re-verify evidence" in content


def test_llm_reply_used_when_available():
    llm = StubLLM(reply="BETTER PROMPT")
    generator = CandidateGenerator(llm=llm)
    kind, content = generator.generate("missed:sql_injection", [{"case_type": "false_negative"}])
    assert content == "BETTER PROMPT"
    assert llm.messages[0][0]["role"] == "system"


def test_llm_failure_falls_back_to_template():
    llm = StubLLM(fail=True)
    generator = CandidateGenerator(llm=llm)
    _, content = generator.generate("missed:sql_injection", [])
    assert GUIDELINES["sql_injection"] in content


def test_blank_llm_reply_falls_back_to_template():
    llm = StubLLM(reply="   ")
    generator = CandidateGenerator(llm=llm)
    _, content = generator.generate("missed:xss", [])
    assert GUIDELINES["xss"] in content
