from maid_agent.goal.models import Condition
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.protocol.models import ActionResult, ActionStatus, StateSnapshot


def test_action_step_goal_use_real_postconditions(snapshot: StateSnapshot) -> None:
    verifier = PostconditionVerifier()
    assert verifier.evaluate(Condition(type="ITEM_COUNT", args={"item_id": "minecraft:oak_log", "count": 6}), snapshot)
    assert not verifier.evaluate(Condition(type="ITEM_COUNT", args={"item_id": "minecraft:iron_ingot", "count": 1}), snapshot)
    result = ActionResult(request_id="r", action_id="a", status=ActionStatus.SUCCESS, code="ARRIVED")
    assert verifier.evaluate(Condition(type="ACTION_CODE", args={"code": "ARRIVED"}), snapshot, result)
    assert verifier.evaluate(Condition(type="CUSTOM", args={"predicate": "food_count", "count": 3}), snapshot)


def test_entity_hidden_or_out_of_range_is_not_treated_as_gone(snapshot: StateSnapshot) -> None:
    verifier = PostconditionVerifier()
    entity_id = "11111111-1111-4111-8111-111111111111"
    hidden = snapshot.model_copy(update={"nearby_entities": [], "entity_presence": {entity_id: "PRESENT_NOT_OBSERVABLE"}})
    assert verifier.evaluate(Condition(type="ENTITY_EXISTS", args={"uuid": entity_id}), hidden)
    assert not verifier.evaluate(Condition(type="ENTITY_GONE", args={"uuid": entity_id}), hidden)
    unknown = snapshot.model_copy(update={"nearby_entities": [], "entity_presence": {entity_id: "UNKNOWN"}})
    assert not verifier.evaluate(Condition(type="ENTITY_EXISTS", args={"uuid": entity_id}), unknown)
    assert not verifier.evaluate(Condition(type="ENTITY_GONE", args={"uuid": entity_id}), unknown)
    dead = snapshot.model_copy(update={"nearby_entities": [], "entity_presence": {entity_id: "DEAD"}})
    assert verifier.evaluate(Condition(type="ENTITY_GONE", args={"uuid": entity_id}), dead)
