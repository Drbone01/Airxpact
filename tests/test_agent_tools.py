def test_calculator_adds_budget_lines(agent):
    assert agent.calculator("864 + 3*120 + 4*85").endswith("= 1,564")


def test_calculator_rejects_code(agent):
    assert "Only numbers" in agent.calculator('__import__("os").system("ls")')


def test_date_list_matches_weekdays(agent):
    out = agent.date_calculator(operation="list", date="2026-12-12", days=3)
    assert out.splitlines() == [
        "Day 1: Saturday 12 December 2026",
        "Day 2: Sunday 13 December 2026",
        "Day 3: Monday 14 December 2026",
    ]


def test_days_between_counts_nights(agent):
    out = agent.date_calculator(operation="days_between", date="2026-12-12", end_date="2026-12-19")
    assert "7 nights" in out


def test_clean_id_strips_unsafe_characters(agent):
    assert agent.clean_id("partner:acme user@1", "x") == "partner-acme-user-1"
    assert agent.clean_id("", "fallback") == "fallback"
