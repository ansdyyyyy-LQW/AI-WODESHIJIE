from maid_ai_control.self_test import run_self_test

def test_self_test():
    result=run_self_test()
    assert result["ok"] is True
