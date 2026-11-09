import json
from datetime import date
from types import SimpleNamespace

import pytest

import main


def test_drinks_are_available_as_a_food_category():
    assert "飲み物" in main.FOOD_CATEGORIES


def test_edit_action_opens_inline_form(tmp_path, monkeypatch):
    food = {
        "id": "food-1",
        "name": "牛乳",
        "expiry_date": "2026-12-01",
        "expiry_type": "best_before",
        "category": "その他",
        "eaten": False,
    }
    data_file = tmp_path / "foods.json"
    data_file.write_text(json.dumps([food], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_FILE", data_file)
    page = SimpleNamespace(
        add=lambda *controls: setattr(page, "controls", controls),
        update=lambda: None,
    )

    main.main(page)

    card = page.controls[0].content.controls[2]
    food_list = card.content.controls[5].content
    edit_button = food_list.controls[0].content.controls[1].controls[2]
    edit_button.on_click(None)

    edited_food_list = card.content.controls[5].content
    edit_fields = edited_food_list.controls[0].content.controls
    assert edit_fields[0].label == "商品名"
    assert edit_fields[1].label == "ジャンル"
    assert edit_fields[2].label == "期限（YYYY-MM-DD）"


def test_mobile_layout_stacks_inputs_and_product_actions(tmp_path, monkeypatch):
    food = {
        "id": "food-1",
        "name": "牛乳",
        "expiry_date": "2026-12-01",
        "expiry_type": "best_before",
        "category": "その他",
        "eaten": False,
    }
    data_file = tmp_path / "foods.json"
    data_file.write_text(json.dumps([food], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_FILE", data_file)
    page = SimpleNamespace(
        width=375,
        add=lambda *controls: setattr(page, "controls", controls),
        update=lambda: None,
    )

    main.main(page)
    page.on_resize(None)

    app_content = page.controls[0].content
    composer = app_content.controls[2].content.controls[0].content
    assert composer.controls[2].content.__class__.__name__ == "Column"
    assert composer.controls[3].content.__class__.__name__ == "Column"

    food_list = app_content.controls[2].content.controls[5].content
    product_details = food_list.controls[0].content.controls
    assert len(product_details) == 3
    action_row = product_details[2]
    assert action_row.controls[1].controls[0].icon == main.ft.Icons.EDIT_OUTLINED


def test_update_food_details_normalizes_name_and_expiry():
    food = {"name": "元の商品", "expiry_date": "2026-12-01", "category": "肉"}

    main.update_food_details(food, "  新しい商品  ", "2027-01-15", "魚")

    assert food == {"name": "新しい商品", "expiry_date": "2027-01-15", "category": "魚"}


def test_update_food_details_uses_default_name_when_name_is_blank():
    food = {"name": "元の商品", "expiry_date": "2026-12-01", "category": "肉"}

    main.update_food_details(food, "  ", date(2027, 1, 15).isoformat(), "肉")

    assert food["name"] == "商品"


@pytest.mark.parametrize(
    ("name", "expiry", "category", "error"),
    [
        ("新しい商品", "not-a-date", "魚", "YYYY-MM-DD"),
        ("新しい商品", "2027-01-15", "不明", "ジャンル"),
    ],
)
def test_update_food_details_rejects_invalid_fields_without_changing_food(name, expiry, category, error):
    food = {"name": "元の商品", "expiry_date": "2026-12-01", "category": "肉"}

    with pytest.raises(ValueError, match=error):
        main.update_food_details(food, name, expiry, category)

    assert food == {"name": "元の商品", "expiry_date": "2026-12-01", "category": "肉"}
