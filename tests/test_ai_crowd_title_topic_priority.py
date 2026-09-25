"""Regression coverage for topic-first session title prompts."""

from api.models import title_from, title_subject_from_message
from api.streaming import (
    _background_title_generation_inputs,
    _fallback_title_from_exchange,
    _title_prompts,
)
from types import SimpleNamespace


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
    reference = "[Storage audit](https://host.test/session/abc) · `@session:moss/abc`"
    qa, prompts = _title_prompts(reference, "")
    assert "User question:\nStorage audit" in qa
    qa, _ = _title_prompts(reference + "\nAgora planeje a migração do banco de dados.", "")
    assert "User question:\nAgora planeje a migração do banco de dados." in qa
    assert "Previous conversation topic (context only): Storage audit" in qa
    for prompt in prompts:
        assert "a new substantive intent takes precedence" in prompt
        assert "an old title is only a fallback" in prompt
        assert "only when the user explicitly asks about them" in prompt


def test_copied_reference_does_not_become_provisional_or_fallback_title():
    reference = (
        "Conversation reference: [Teste E2E autorizado em produção por Rodolfo\\. Identificador excl]"
        "(https://hermes.example/session/b79a286775ff)\n"
        "Internal session: `@session:moss/b79a286775ff`"
    )
    message = reference + "\n\nMelhore a UX dos títulos de conversas copiadas."
    assert title_from([{"role": "user", "content": message}]) == "Melhore a UX dos títulos de conversas copiadas."
    assert _fallback_title_from_exchange(message, "") == "Melhore dos títulos conversas"
    qa, _ = _title_prompts(message, "")
    assert qa.startswith("User question:\nMelhore a UX dos títulos")
    assert "Previous conversation topic (context only): Teste E2E autorizado" in qa
    assert "@session:" not in qa
    assert title_from([{"role": "user", "content": reference}]).startswith("Teste E2E autorizado")
    messages = [{"role": "user", "content": message}, {"role": "assistant", "content": "Vou ajustar a UX."}]
    session = SimpleNamespace(messages=messages, title=title_from(messages), llm_title_generated=False)
    inputs = _background_title_generation_inputs(session)
    assert inputs is not None
    assert inputs[0].startswith("Conversation reference:")
    assert _title_prompts(*inputs)[0].startswith("User question:\nMelhore a UX")


def test_compact_reference_and_non_reference_prose_preserve_intent():
    compact = "[Old topic](https://host.test/session/abc) · `@session:roy/abc`"
    assert title_from([{"role": "user", "content": compact + "\nNovo assunto: backup."}]) == "Novo assunto: backup."
    assert title_subject_from_message("Analise [a fonte](https://host.test/session/abc) agora") == "Analise [a fonte](https://host.test/session/abc) agora"
    assert title_subject_from_message("[externo](https://host.test/article/abc) e explique") == "[externo](https://host.test/article/abc) e explique"


def test_title_prompts_preserve_language_and_output_guards():
    _qa, prompts = _title_prompts(
        "Por que a esteira de deploy falhou?",
        "A imagem e o seletor de runtime estavam divergentes.",
    )

    assert all("Match the language of the user question." in prompt for prompt in prompts)
    assert all("title" in prompt.lower() for prompt in prompts)
    assert all("markdown" in prompt.lower() for prompt in prompts)