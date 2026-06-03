from __future__ import annotations

from kaggle_benchmarks.actors import LLMChat
from kaggle_benchmarks.actors.llms import LLMResponse

from kagraph import (
    HumanMessage,
    ImageMessage,
    coerce_messages,
    image_from_base64,
    invoke_llm,
    prompt_llm,
)


class CapturingLLM(LLMChat):
    def __init__(self):
        super().__init__(name="capturing")
        self.seen_messages = []

    def invoke(self, messages, system=None, **kwargs):
        self.seen_messages.append(list(messages))
        return LLMResponse(content="ok")


def test_prompt_llm_sends_image_before_text_prompt():
    llm = CapturingLLM()
    image = image_from_base64("aGVsbG8=", format="png")

    result = prompt_llm(llm, "Describe this screenshot.", image=image)

    assert result == "ok"
    assert len(llm.seen_messages) == 1
    sent = llm.seen_messages[0]
    assert [message.sender.role for message in sent] == ["user", "user"]
    assert sent[0].payload == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aGVsbG8="},
        }
    ]
    assert sent[1].content == "Describe this screenshot."


def test_invoke_llm_preserves_message_history_then_image_then_prompt():
    llm = CapturingLLM()
    image = image_from_base64("aGVsbG8=", format="png")

    invoke_llm(
        llm,
        messages=[HumanMessage("Existing context.")],
        prompt="What changed?",
        image=image,
    )

    sent = llm.seen_messages[0]
    assert [message.sender.role for message in sent] == ["user", "user", "user"]
    assert sent[0].content == "Existing context."
    assert sent[1].payload[0]["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
    assert sent[2].content == "What changed?"


def test_openai_style_image_content_blocks_expand_to_kbench_messages():
    messages = coerce_messages(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Please inspect this screenshot."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        }
    )

    assert [message.sender.role for message in messages] == ["user", "user"]
    assert messages[0].content == "Please inspect this screenshot."
    assert messages[1].payload[0]["image_url"]["url"] == "data:image/png;base64,aGVsbG8="


def test_image_message_creates_visible_user_image_message():
    image = image_from_base64("aGVsbG8=", format="png")
    message = ImageMessage(image)

    assert message.sender.role == "user"
    assert message.is_visible_to_llm is True
    assert message.payload[0]["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
