import pytest

from jarvis_brain.wakeword import strip_wake_word


@pytest.mark.parametrize("heard,expected", [
    ("Jarvis, quelle heure est-il ?", "quelle heure est-il ?"),
    ("Hé Jarvis. Éteins la télé.", "Éteins la télé."),
    ("Djarvis, météo", "météo"),
    ("Jarvis.", ""),
    ("Okay Jarvis", ""),
])
def test_wake_word_detected(heard, expected):
    assert strip_wake_word(heard) == expected


@pytest.mark.parametrize("heard", ["Bonjour tout le monde", "On regarde Jarvis ce soir ?", ""])
def test_wake_word_absent(heard):
    assert strip_wake_word(heard) is None
