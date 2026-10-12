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



def test_demo_keys_last_as_long_as_the_snapshot(tmp_path):
    """Saved rows hold ciphertexts made with the demo redaction key: a restart must reuse it, or they become unreadable."""
    from app.demo import _demo_keys

    snapshot = str(tmp_path / "demo.json")
    first = _demo_keys(snapshot)("redaction_key", lambda: "key-1")
    again = _demo_keys(snapshot)("redaction_key", lambda: "key-2")       # a restart
    assert first == again == "key-1"
    assert _demo_keys("")("redaction_key", lambda: "fresh") == "fresh"    # no snapshot: ephemeral, nothing written
    assert [p.name for p in tmp_path.iterdir()] == ["demo.json.keys.json"]
