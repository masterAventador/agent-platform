from uuid import UUID, uuid4

from agent_platform.platform.conversations.entities import conversation_followup_run_id


def test_followup_run_id_is_deterministic_for_conversation_and_trigger_message() -> None:
    conversation_id = uuid4()
    trigger_message_id = uuid4()

    first = conversation_followup_run_id(
        conversation_id=conversation_id, trigger_message_id=trigger_message_id
    )
    second = conversation_followup_run_id(
        conversation_id=conversation_id, trigger_message_id=trigger_message_id
    )

    assert isinstance(first, UUID)
    assert first == second


def test_followup_run_id_differs_across_conversations_and_messages() -> None:
    conversation_id = uuid4()
    trigger_message_id = uuid4()
    base = conversation_followup_run_id(
        conversation_id=conversation_id, trigger_message_id=trigger_message_id
    )

    assert base != conversation_followup_run_id(
        conversation_id=uuid4(), trigger_message_id=trigger_message_id
    )
    assert base != conversation_followup_run_id(
        conversation_id=conversation_id, trigger_message_id=uuid4()
    )
