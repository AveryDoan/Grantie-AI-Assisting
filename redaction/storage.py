"""Step 5: store and load the token map (originals encrypted).

Table redaction_token_maps - one row per distinct (text, token, source, original):
  token, entity_type, source (field name or document id), text_key
  (which stored text the spans belong to), original_value_encrypted
  (AES-256-GCM, see redaction.crypto), spans (positions only - no text),
  ordinal (order of first appearance, so canonical forms and numbering
  survive a round trip).

Saving replaces the application's previous rows (idempotent re-runs).
Nothing here logs or returns plaintext except load_token_map(), whose result
is for officer views and quote mapping only.
"""

from __future__ import annotations

from typing import Any

from redaction.config import RedactionConfig, default_config
from redaction.crypto import TokenCipher
from redaction.tokenizer import Occurrence, TokenEntry, TokenMap

TABLE = "redaction_token_maps"


def _text_order(token_map: TokenMap) -> list[str]:
    keys = list(token_map.occurrences)
    return sorted(keys, key=lambda k: (k != "application_text", k))


def token_map_rows(application_id: str, run_id: str | None, token_map: TokenMap, cipher: TokenCipher) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    ordinal = 0
    for text_key in _text_order(token_map):
        for o in sorted(token_map.occurrences[text_key], key=lambda o: o.red_start):
            key = (text_key, o.token, o.source, o.original)
            if key not in grouped:
                grouped[key] = {
                    "application_id": application_id,
                    "run_id": run_id,
                    "token": o.token,
                    "entity_type": token_map.entries[o.token].token_type,
                    "source": o.source,
                    "text_key": text_key,
                    "original_value_encrypted": cipher.encrypt(application_id, o.token, o.original),
                    "spans": [],
                    "ordinal": ordinal,
                }
                ordinal += 1
            grouped[key]["spans"].append([o.red_start, o.red_end, o.orig_start, o.orig_end])
    return list(grouped.values())


def save_token_map(store: Any, application_id: str, run_id: str | None, token_map: TokenMap, cipher: TokenCipher) -> int:
    rows = token_map_rows(application_id, run_id, token_map, cipher)  # encrypt first: fail before deleting anything
    store.delete(TABLE, eq={"application_id": application_id})
    if rows:
        store.insert(TABLE, rows)
    return len(rows)


def load_token_map(store: Any, application_id: str, cipher: TokenCipher, cfg: RedactionConfig | None = None) -> TokenMap:
    cfg = cfg or default_config()
    tm = TokenMap(cfg)
    rows = sorted(store.select(TABLE, eq={"application_id": application_id}), key=lambda r: r["ordinal"])
    for r in rows:
        original = cipher.decrypt(application_id, r["token"], r["original_value_encrypted"])
        token = r["token"]
        if token not in tm.entries:
            canonical = token if token.startswith("[LOCATION:") else original
            tm.entries[token] = TokenEntry(token, r["entity_type"], canonical)
        entry = tm.entries[token]
        if not token.startswith("[LOCATION:") and original not in entry.variants:
            entry.variants.append(original)
        if r["source"] not in entry.sources:
            entry.sources.append(r["source"])
        for rs, re_, os_, oe in r["spans"]:
            tm.occurrences.setdefault(r["text_key"], []).append(Occurrence(token, original, r["source"], rs, re_, os_, oe))
    for occs in tm.occurrences.values():
        occs.sort(key=lambda o: o.red_start)
    if not tm.entries:
        return tm
    for token, entry in tm.entries.items():  # keep numbering consistent for any later additions
        if not token.startswith("[LOCATION:"):
            n = int(token.rsplit("_", 1)[1].rstrip("]"))
            tm._counters[entry.token_type] = max(tm._counters.get(entry.token_type, 0), n)
    return tm
