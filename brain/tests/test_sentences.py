from jarvis_brain.sentences import SentenceSplitter, clean_for_speech


def feed_all(chunks):
    s = SentenceSplitter()
    out = []
    for c in chunks:
        out += s.feed(c)
    return out + s.flush()


def test_splits_streamed_sentences():
    chunks = ["Bonjour Mon", "sieur. Il fait 3.5 degrés", " dehors ! Voulez-vous", " un parapluie ?"]
    assert feed_all(chunks) == [
        "Bonjour Monsieur.",
        "Il fait 3.5 degrés dehors !",
        "Voulez-vous un parapluie ?",
    ]


def test_keeps_abbreviations_and_short_fragments():
    assert feed_all(["J'ai vu M. Dupont hier soir. Oui."]) == ["J'ai vu M. Dupont hier soir.", "Oui."]


def test_long_text_without_punctuation_is_cut():
    parts = feed_all(["mot " * 200])
    assert len(parts) > 1
    assert all(len(p) <= 301 for p in parts)


def test_clean_for_speech_removes_markdown():
    assert clean_for_speech("**Attention** : `code` et [lien](http://x)") == "Attention : code et lien"
    assert clean_for_speech("- un\n- deux") == "un deux"
