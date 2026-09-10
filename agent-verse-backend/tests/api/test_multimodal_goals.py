"""Test multi-modal goal submission with image attachments."""


def test_goal_request_accepts_image_url():
    from app.api.goals import GoalRequest
    r = GoalRequest(goal="Analyze this chart", image_url="https://example.com/chart.png")
    assert r.image_url == "https://example.com/chart.png"


def test_goal_request_defaults_none():
    from app.api.goals import GoalRequest
    r = GoalRequest(goal="plain text goal")
    assert r.image_url is None
    assert r.attachment_base64 is None
    assert r.attachment_mime is None
