import main


def test_blank_name_falls_back_to_generic_label():
    assert main.normalize_name("") == "商品"
    assert main.normalize_name("   ") == "商品"
    assert main.normalize_name("牛乳") == "牛乳"
