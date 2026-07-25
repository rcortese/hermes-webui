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


def test_title_prompts_preserve_language_and_output_guards():
    _qa, prompts = _title_prompts(
        "Por que a esteira de deploy falhou?",
        "A imagem e o seletor de runtime estavam divergentes.",
    )

    assert all("Match the language of the user question." in prompt for prompt in prompts)
    assert all("title" in prompt.lower() for prompt in prompts)
    assert all("markdown" in prompt.lower() for prompt in prompts)