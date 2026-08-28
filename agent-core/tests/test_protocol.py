from maid_agent.protocol.models import MessageType, ProtocolEnvelope, StateSnapshot


def test_protocol_roundtrip(snapshot: StateSnapshot) -> None:
    env = ProtocolEnvelope.make(MessageType.STATE_SNAPSHOT, snapshot.model_dump(mode="json"), maid_uuid="maid-1")
    restored = ProtocolEnvelope.model_validate_json(env.model_dump_json())
    assert restored.type == MessageType.STATE_SNAPSHOT
    assert restored.maid_uuid == "maid-1"
    assert StateSnapshot.model_validate(restored.payload).item_count("minecraft:oak_log") == 6
