from ikg.normalize import fold, roman_to_int, tokenize


def test_fold_strips_diacritics_and_quotes():
    assert fold("Rávan’s Vishṇu Parāçara") == "Ravan's Vishnu Paracara"


def test_tokenize_drops_stopwords_and_folds():
    assert tokenize("O Ráma, thou art the Lord of Ayodhyá") == ["rama", "lord", "ayodhya"]


def test_roman_to_int():
    assert [roman_to_int(n) for n in ("I", "IV", "IX", "XL", "CCCLXV")] == [1, 4, 9, 40, 365]
