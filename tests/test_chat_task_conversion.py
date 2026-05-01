from taos.core.tasks.chat_task_intent import detect_chat_task_intent


def test_detect_chat_task_intent_daily():
    result = detect_chat_task_intent("Track AI news daily")
    assert result.is_task_intent is True
    assert result.interval_seconds == 86400
    assert result.trigger_type == "time_based"


def test_detect_chat_task_intent_non_task():
    result = detect_chat_task_intent("Explain transformers in simple terms")
    assert result.is_task_intent is False
    assert result.interval_seconds == 0


def test_detect_chat_task_intent_custom_interval():
    result = detect_chat_task_intent("Monitor bitcoin price every 2 hours")
    assert result.is_task_intent is True
    assert result.interval_seconds == 7200


def test_detect_chat_task_intent_send_me_linkedin_link_is_not_task():
    result = detect_chat_task_intent("ok then send me his linkedin acc link")
    assert result.is_task_intent is False
    assert result.interval_seconds == 0


def test_detect_chat_task_intent_send_me_with_schedule_is_task():
    result = detect_chat_task_intent("send me OpenAI CEO updates every day")
    assert result.is_task_intent is True
    assert result.interval_seconds == 86400
