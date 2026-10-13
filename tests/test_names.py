from blindfold.names import cells, find_label_columns, find_labels, find_name_shapes
from blindfold.normalize import display_form, entity_key, split_uz_suffix, to_cyrillic, to_latin


def vals(spans):
    return [(s.type, s.value) for s in spans]


def test_transliteration_and_suffix():
    assert to_latin("Ўғли Шоҳрух") == "o'g'li shohrux"
    assert to_cyrillic("Karimov") == "каримов"
    assert split_uz_suffix("Karimovga") == ("Karimov", "ga")
    assert split_uz_suffix("Karimovning") == ("Karimov", "ning")
    assert split_uz_suffix("Olga") == ("Olga", "")


def test_entity_key_merges_spellings_and_cases():
    k = entity_key("Person", "Karimov")
    assert entity_key("Person", "Karimovga") == k
    assert entity_key("Person", "Каримову") == k
    assert entity_key("Person", "Karimova") != k
    assert entity_key("Phone", "+998 90 123-45-67") == entity_key("Phone", "998901234567")


def test_entity_key_surname_cross_script():
    assert entity_key("Person", "Каримов") == entity_key("Person", "Karimov")


def test_entity_key_leaves_ordinary_words_untouched():
    # These have no Surn-tagged pymorphy3 parse at all (unlike "Каримов"), so ru_name
    # must fall back to a plain transliteration, not a spurious Name-tagged reading
    # dug out of a low-scored parse ("вера" instead of "вери", etc).
    for w in ("веры", "вере", "любови", "тома"):
        assert entity_key("Person", w) == (to_latin(split_uz_suffix(w)[0]),)


def test_display_form_russian_nominative():
    assert display_form("Person", "Каримову Азизу") == "Каримов Азиз"
    assert display_form("Person", "Tursunov Bobur") == "Tursunov Bobur"


def test_labels_person_and_address():
    got = vals(find_labels("Mijoz: Tursunov Bobur, 1988\nManzil: Toshkent sh., Chilonzor t., 5-uy\n"))
    assert ("Person", "Tursunov Bobur") in got
    assert ("Address", "Toshkent sh., Chilonzor t., 5-uy") in got
    assert ("Person", "Иванов Сергей") in vals(find_labels("ФИО: Иванов Сергей; дата"))


def test_label_columns_in_pandas_output():
    text = "   F.I.Sh.        Summa\n0  Karimov Aziz    1200\n1  Rashidova Dilnoza  900\n"
    got = vals(find_label_columns(text))
    assert ("Person", "Karimov Aziz") in got and ("Person", "Rashidova Dilnoza") in got


def test_cells_split():
    assert len(cells("a\tb\tc")) == 3
    assert len(cells("0  Karimov Aziz    1200")) == 3
    assert len(cells("Hello, world")) == 1


def test_name_shapes():
    got = vals(find_name_shapes("Tursunov Bobur Alisherovich keldi. Aziz Rustam o'g'li ham. "
                                "Imzo: A.B. Karimov. Karimovga xat. To'lov va so'rov qabul qilindi. "
                                "\u00abOrient Trade\u00bb MChJ hisobi. Клиенту Каримову позвонили."))
    joined = " | ".join(v for _, v in got)
    assert "Tursunov Bobur Alisherovich" in joined
    assert "Aziz Rustam o'g'li" in joined
    assert "A.B. Karimov" in joined
    assert "Karimovga" in joined
    assert "\u00abOrient Trade\u00bb MChJ" in joined
    assert "Каримову" in joined
    assert "To'lov" not in joined and "so'rov" not in joined


def test_v11_suffix_split_needs_surname_shape_or_known_first_name():
    assert split_uz_suffix("Malika") == ("Malika", "")
    assert split_uz_suffix("Malikaga") == ("Malika", "ga")
    assert split_uz_suffix("Karimovga") == ("Karimov", "ga")
    assert split_uz_suffix("Olga") == ("Olga", "")
    assert split_uz_suffix("Sobirovaning") == ("Sobirova", "ning")
    assert split_uz_suffix("Каримовга") == ("Каримов", "га")
    assert split_uz_suffix("Маликага") == (
        "Малика", "га")


LONE = ["Malika", "Dilnoza", "Jasur", "Otabek", "Umid", "Sardor", "Lola"]


def test_v11_lone_first_names_detected():
    for n in LONE:
        text = f"Kecha {n} filialga keldi va ariza berdi."
        assert ("Person", n) in vals(find_name_shapes(text)), n
    assert ("Person", "Malikaga") in vals(find_name_shapes("Biz Malikaga xat yozdik."))


def test_v11_first_name_widens_to_following_surname():
    assert ("Person", "Malika Rashidova") in vals(find_name_shapes("Mijoz bilan Malika Rashidova gaplashdi."))


def test_v11_lowercase_words_and_cities_are_not_names():
    assert find_name_shapes("Biz katta umid bilan kutamiz.") == []
    assert find_name_shapes("Toshkent") == []
    assert find_name_shapes("Bog'da lola gullari ochildi.") == []


def test_v11_lexicon_file_sizes():
    from blindfold.normalize import FIRST_NAMES, LEXICON_LINES
    assert LEXICON_LINES[0] == "# Common public given names, compiled by hand; extend locally."
    names = [x for x in LEXICON_LINES if x and not x.startswith("#")]
    latin = [x for x in names if x.isascii()]
    cyr = [x for x in names if not x.isascii()]
    assert len(latin) >= 250 and len(cyr) >= 100
    assert all(x == x.lower() for x in names)
    assert {"malika", "малика", "сергей"} <= FIRST_NAMES
