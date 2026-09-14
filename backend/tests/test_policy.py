"""Memory policy: durable knowledge in, transient chatter out."""
import pytest

from app.memory import policy


@pytest.mark.parametrize("text", [
    "I prefer concise technical explanations.",
    "My name is Alex.",
    "I am building a Kubernetes monitoring system.",
    "Remember that I like dark mode.",
])
def test_durable_statements_are_saved(text):
    assert policy.evaluate(text).is_durable, text


@pytest.mark.parametrize("text", [
    "What's the weather today?",
    "Explain deployment pipelines.",
    "What do you remember about my projects?",
    "hi",
    "thanks",
])
def test_transient_statements_are_rejected(text):
    assert not policy.evaluate(text).is_durable, text


def test_categories_are_sensible():
    assert policy.evaluate("My name is Alex.").category == "IDENTITY"
    assert policy.evaluate("I am building a Kubernetes cluster.").category == "PROJECT"
    assert policy.classify("I have switched to light mode.") == "PREFERENCE"


def test_change_detection():
    assert policy.looks_like_change("Actually, I switched to light mode.")
    assert not policy.looks_like_change("I like dark mode.")
