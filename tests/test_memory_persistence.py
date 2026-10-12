from app.store.memory import MemoryStore


def test_memory_store_persists_rows_and_files(tmp_path):
    path = tmp_path / "demo.json"
    store = MemoryStore(path)
    row = store.insert("applications", {"id": "application_1", "status": "submitted"})[0]
    store.upload("documents", "application_1/file.pdf", b"pdf", "application/pdf")

    loaded = MemoryStore.load(path)

    assert loaded.select("applications", eq={"id": row["id"]})[0]["status"] == "submitted"
    assert loaded.download("documents", "application_1/file.pdf") == b"pdf"


def test_memory_store_continues_review_sequence_after_loading(tmp_path):
    path = tmp_path / "demo.json"
    store = MemoryStore(path)
    store.insert("officer_reviews", {"id": "review_1"})

    loaded = MemoryStore.load(path)

    assert loaded.insert("officer_reviews", {"id": "review_2"})[0]["seq"] == 2
