import main


def test_expiry_types_include_usage_expiry():
    assert "use_date" in main.EXPIRY_TYPES
    assert main.EXPIRY_TYPES["use_date"] == "使用期限"


def test_supported_expiry_types_cover_food_and_daily_products():
    expected = {"best_before", "use_by", "use_date"}
    assert set(main.EXPIRY_TYPES) >= expected
