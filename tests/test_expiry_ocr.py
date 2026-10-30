import main


def test_extract_expiry_from_text_best_before():
    expiry_date, expiry_type = main.extract_expiry_from_text("賞味期限 2026/12/31")
    assert expiry_date == "2026-12-31"
    assert expiry_type == "best_before"


def test_extract_expiry_from_text_use_by():
    expiry_date, expiry_type = main.extract_expiry_from_text("消費期限 2026-08-15")
    assert expiry_date == "2026-08-15"
    assert expiry_type == "use_by"


def test_extract_expiry_from_text_use_date():
    expiry_date, expiry_type = main.extract_expiry_from_text("使用期限 2027.01.10")
    assert expiry_date == "2027-01-10"
    assert expiry_type == "use_date"
