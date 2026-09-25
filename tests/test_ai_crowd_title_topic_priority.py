"""Regression coverage for topic-first session title prompts."""

from api.streaming import _title_prompts


def test_title_prompts_prioritize_substantive_topic_over_workflow():
    qa, prompts = _title_prompts(
        "Audit the deployment method for a broken release",
        "The release failed because runtime and build inputs were coupled",
    )

    assert "Audit the deployment method" in qa
    assert len(prompts) == 2
    assert all("Do not prioritize method, format, tool, role" in prompt for prompt in prompts)
    assert any("main topic and substantive intent" in prompt for prompt in prompts)
    assert all("unless it is itself the central subject" in prompt for prompt in prompts)
    assert all("references, URLs, profile names, and session IDs as non-binding transport/context" in prompt for prompt in prompts)
    assert all("a new substantive intent takes precedence" in prompt for prompt in prompts)
    assert all("only when the user explicitly asks about them" in prompt for prompt in prompts)


def test_pasted_reference_keeps_new_intent_and_old_title_as_fallback():
    reference = (
        "Conversation reference: [Storage audit](https://host.test/session/abc)\n"
        "Internal session: `@session:moss/abc`"
    )
    for question in [reference, reference + "\nAgora planeje a migração do banco de dados."]:
        qa, prompts = _title_prompts(question, "")
        assert question in qa
        assert all("a new substantive intent takes precedence" in p for p in prompts)
        assert all("an old title is only a fallback" in p for p in prompts)
        assert all("only when the user explicitly asks about them" in p for p in prompts)


def test_title_prompts_preserve_language_and_output_guards():
    _qa, prompts = _title_prompts(
        "Por que a esteira de deploy falhou?",
        "A imagem e o seletor de runtime estavam divergentes.",
    )

    assert all("Match the language of the user question." in prompt for prompt in prompts)
    assert all("title" in prompt.lower() for prompt in prompts)
    assert all("markdown" in prompt.lower() for prompt in prompts)