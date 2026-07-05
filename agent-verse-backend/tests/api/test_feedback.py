def test_goals_api_has_feedback_endpoint():
    import inspect
    from app.api import goals
    source = inspect.getsource(goals)
    assert "/feedback" in source, "RLHF feedback endpoint missing"
