from response_policy import (
    PRODUCT_EVALUATION_SYSTEM_PROMPT,
    PURCHASING_AGENT_SYSTEM_PROMPT,
    normalize_general_response,
)


def test_user_facing_policy_forbids_ungrounded_shopping_claims_and_tables():
    assert "Never invent product facts" in PURCHASING_AGENT_SYSTEM_PROMPT
    assert "markdown tables" in PURCHASING_AGENT_SYSTEM_PROMPT
    assert "fewer than 80 words" in PURCHASING_AGENT_SYSTEM_PROMPT


def test_product_evaluation_policy_is_fact_bounded_and_separate_from_general_chat():
    assert "Use only the supplied records" in PRODUCT_EVALUATION_SYSTEM_PROMPT
    assert PRODUCT_EVALUATION_SYSTEM_PROMPT != PURCHASING_AGENT_SYSTEM_PROMPT


def test_general_response_normalization_never_cuts_at_a_word_limit_without_sentence_end():
    short = "One short sentence."
    assert normalize_general_response(short) == short
    no_boundary = " ".join("word" for _ in range(101))
    assert normalize_general_response(no_boundary) == no_boundary
