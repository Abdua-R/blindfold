from blindfold.ids import find_structured, luhn_ok, pinfl_ok


def types_of(text):
    return [(s.type, s.value) for s in find_structured(text)]


def test_pinfl_checksum_official_examples():
    assert pinfl_ok("31210932040247")
    assert pinfl_ok("40201902050010")
    assert not pinfl_ok("31210932040248")
    assert not pinfl_ok("71210932040247")


def test_luhn():
    assert luhn_ok("8600123456789012")
    assert not luhn_ok("8600123456789013")


def test_pinfl_in_text_plain_and_spaced():
    assert ("PINFL", "31505883017120") in types_of("JShShIR 31505883017120 dan")
    assert ("PINFL", "4 220791 402 005 4") in types_of("pinfl: 4 220791 402 005 4.")


def test_card_uzcard_and_humo_and_masked():
    assert ("Card", "8600 1234 5678 9012") in types_of("karta 8600 1234 5678 9012")
    assert ("Card", "9860111122223337") in types_of("Humo 9860111122223337")
    assert ("Card", "8600 12** **** 9012") in types_of("card 8600 12** **** 9012")


def test_account_by_plan_code():
    assert ("Account", "20208 000 9 00123456 001") in types_of("h/r 20208 000 9 00123456 001")
    assert ("Account", "20208000900123456001") in types_of("x 20208000900123456001 y")


def test_phone_forms():
    assert ("Phone", "+998 90 123-45-67") in types_of("tel +998 90 123-45-67")
    assert ("Phone", "(71) 240-27-72") in types_of("тел. (71) 240-27-72")
    assert ("Phone", "901234567") in types_of("raqam 901234567")


def test_stir_and_amount_exemption():
    assert ("STIR", "305123456") in types_of("STIR: 305123456")
    assert types_of("summa 250000000 so'm") == []
    assert types_of("summa 250000000.50") == []


def test_passport_latin_and_cyrillic_lookalikes():
    assert ("Passport", "AA1234567") in types_of("pasport AA1234567")
    assert ("Passport", "АА1234567") in types_of("паспорт АА1234567")


def test_email_plate_and_user_path():
    assert ("Email", "b.tursunov@mail.uz") in types_of("email b.tursunov@mail.uz")
    assert ("Plate", "01 A 123 BC") in types_of("avto 01 A 123 BC")
    assert ("Person", "rtursunov") in types_of(r"C:\Users\rtursunov\.claude\projects\x")
    assert ("Person", "rtursunov") in types_of("/home/rtursunov/data")


def test_plain_numbers_are_not_identifiers():
    assert types_of("2024 yil 15 ta kredit, jami 1 250 000") == []


def test_amount_suffix_variants():
    assert types_of("summa 250000000 so'm") == [] and types_of("summa 250000000 soʻm") == []
