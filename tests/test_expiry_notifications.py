from datetime import date, timedelta

import main


def test_get_expiring_foods_includes_items_due_within_three_days():
    today = date(2026, 11, 2)
    foods = [
        {"name": "今日", "expiry_date": today.isoformat(), "eaten": False},
        {"name": "3日後", "expiry_date": (today + timedelta(days=3)).isoformat(), "eaten": False},
        {"name": "4日後", "expiry_date": (today + timedelta(days=4)).isoformat(), "eaten": False},
        {"name": "使用済み", "expiry_date": today.isoformat(), "eaten": True},
    ]

    expiring = main.get_expiring_foods(foods, today)

    assert [food["name"] for food in expiring] == ["今日", "3日後"]


def test_get_expiring_foods_includes_overdue_items():
    today = date(2026, 11, 2)
    foods = [
        {"name": "期限切れ", "expiry_date": (today - timedelta(days=1)).isoformat(), "eaten": False},
    ]

    assert main.get_expiring_foods(foods, today) == foods
