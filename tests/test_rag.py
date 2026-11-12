from __future__ import annotations

from server.rag import KnowledgeBase, tokenize


def test_persian_tokenizer_normalizes_arabic_letters() -> None:
    assert tokenize("يک كتاب") == tokenize("یک کتاب")


def test_search_ranks_relevant_local_document_and_survives_reopen(tmp_path) -> None:
    base = KnowledgeBase(tmp_path / "knowledge", chunk_chars=240)
    unrelated = base.add("آشپزی", "طرز تهیه نان و سوپ برای شام خانوادگی.")
    relevant = base.add("سخت‌افزار", "کارت گرافیک GeForce 940MX دارای دو گیگابایت حافظه VRAM است.")

    results = base.search("حافظه VRAM کارت گرافیک 940MX")

    assert results[0]["id"] == relevant["id"]
    assert "دو گیگابایت" in results[0]["text"]
    assert unrelated["id"] != relevant["id"]
    assert {item["title"] for item in KnowledgeBase(tmp_path / "knowledge").list()} == {
        "آشپزی", "سخت‌افزار"
    }


def test_delete_rejects_path_traversal_and_removes_document(tmp_path) -> None:
    base = KnowledgeBase(tmp_path)
    item = base.add("راهنما", "متن محلی قابل بازیابی")
    assert base.delete("../" + item["id"]) is False
    assert base.delete(item["id"]) is True
    assert base.search("قابل بازیابی") == []


def test_search_empty_or_unmatched_query_returns_no_results(tmp_path) -> None:
    base = KnowledgeBase(tmp_path)
    base.add("سند", "دربارهٔ کارت گرافیک و حافظه")
    assert base.search("!!!") == []
    assert base.search("موسیقی") == []
