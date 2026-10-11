// Reference lists: the official lookup lists that rules read (for example the NT skilled occupation priority list).
// An officer pastes or uploads a new edition, checks the preview, then saves it. Nothing is saved by the preview.
// A saved list changes the inputs of every assessment, so applications need to be re-assessed to use it.
import { useRef, useState } from "react";
import { api, type ReferenceListPreview, type ReferenceListSummary } from "../api";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../ui";

const EXAMPLE = "Priority occupations\n111531 Research and Development Manager 1\n111631 Quality Assurance Manager 1";

function Browse({ name }: { name: string }) {
  const [q, setQ] = useState("");
  const { data, error } = useLoad(() => api.referenceList(name, q.trim()), [name, q]);
  return (
    <div className="rl-browse">
      <label className="field"><span>Search this list</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Occupation or code" /></label>
      <ErrorNotice error={error} />
      {!data && !error && <Loading />}
      {data && (
        <div className="rl-table" role="region" aria-label="List entries" tabIndex={0}>
          <table>
            <thead><tr><th>Code</th><th>Occupation</th><th>Skill level</th><th>Tier</th></tr></thead>
            <tbody>
              {data.entries.map((e, i) => (
                <tr key={`${e.code}-${i}`}><td>{e.code || "—"}</td><td>{e.name}</td><td>{e.skill_level ?? "—"}</td><td>{e.tier || "—"}</td></tr>
              ))}
              {data.entries.length === 0 && <tr><td colSpan={4}>Nothing matches.</td></tr>}
            </tbody>
          </table>
          {data.entries_truncated && <p className="muted">Showing the first 1000. Search to narrow the list.</p>}
        </div>
      )}
    </div>
  );
}

function Editor({ list, done }: { list: ReferenceListSummary; done: () => void }) {
  const [text, setText] = useState("");
  const [edition, setEdition] = useState("");
  const [source, setSource] = useState("");
  const [preview, setPreview] = useState<ReferenceListPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const edit = (t: string) => { setText(t); setPreview(null); setSaved(null); };
  const upload = async (f: File | undefined) => {
    if (!f) return;
    if (!/\.(txt|csv)$/i.test(f.name)) { setError(new Error("Upload a .txt or .csv file, or paste the list. For a PDF, copy its text.")); return; }
    setError(null); edit(await f.text());
  };
  const check = async () => {
    setBusy(true); setError(null);
    try { setPreview(await api.previewReferenceList(list.name, text)); } catch (e) { setError(e); setPreview(null); }
    setBusy(false);
  };
  const save = async () => {
    setBusy(true); setError(null);
    try {
      const out = await api.saveReferenceList(list.name, { text, edition: edition.trim() || undefined, source: source.trim() || undefined });
      setSaved(`Saved ${out.count} entries. Re-assess applications to use the new list.`);
      setText(""); setPreview(null); done();
    } catch (e) { setError(e); }
    setBusy(false);
  };

  return (
    <div className="rl-editor">
      <h3>Replace this list with a new edition</h3>
      <p className="muted">Paste the published list, one row per line: OSCA code, occupation, skill level. Headings such as “High priority occupations” set the tier.
        Page headers and footers are ignored. The whole list is replaced.</p>
      <label className="field"><span>List text</span>
        <textarea rows={8} value={text} onChange={(e) => edit(e.target.value)} placeholder={EXAMPLE} /></label>
      <div className="rl-row">
        <input ref={file} type="file" accept=".txt,.csv,text/plain,text/csv" hidden onChange={(e) => void upload(e.target.files?.[0])} />
        <Button variant="secondary" onClick={() => file.current?.click()}>Upload .txt or .csv</Button>
        <label className="field"><span>Edition</span><input value={edition} onChange={(e) => setEdition(e.target.value)} placeholder="31 August 2026" maxLength={80} /></label>
        <label className="field"><span>Source (optional)</span><input value={source} onChange={(e) => setSource(e.target.value)} maxLength={300} /></label>
      </div>
      <ErrorNotice error={error} />
      {saved && <div className="notice blue" role="status"><Icon name="check" />{saved}</div>}
      <div className="rule-actions">
        <Button variant="secondary" disabled={busy || !text.trim()} onClick={() => void check()}>Check the list</Button>
      </div>
      {preview && (
        <div className="rl-preview" aria-live="polite">
          <h3>Preview: nothing is saved yet</h3>
          <ul className="cx-list">
            <li>{preview.count} entries read{Object.keys(preview.tiers).length > 0 && ` (${Object.entries(preview.tiers).map(([t, n]) => `${n} ${t.toLowerCase()}`).join(", ")})`}. The list now has {preview.current_count}.</li>
            <li>{preview.added_count} new, {preview.removed_count} removed.</li>
          </ul>
          {preview.warnings.length > 0 && (<><h3>Check these rows</h3><ul className="cx-list">{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul></>)}
          {preview.removed_count > 0 && <p className="muted">Removed, for example: {preview.removed.slice(0, 5).join("; ")}{preview.removed_count > 5 ? "…" : ""}</p>}
          <div className="rule-actions">
            <Button disabled={busy} onClick={() => void save()}>Save as new edition</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function ListCard({ list, reload }: { list: ReferenceListSummary; reload: () => void }) {
  const [open, setOpen] = useState<"browse" | "edit" | null>(null);
  return (
    <section className="panel" aria-labelledby={`list-${list.name}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{list.used_by.length ? `Used by rule ${list.used_by.join(", ")}` : "Not used by any rule"}</p>
          <h2 id={`list-${list.name}`}>{list.description ?? list.name}</h2>
          <p>{list.loaded ? `${list.count} entries${list.edition ? ` · edition ${list.edition}` : ""}` : "Not loaded yet. Rules that need it return Unclear."}
            {list.updated_at && ` · updated ${formatDate(list.updated_at)}`}</p>
          {Object.keys(list.tiers).length > 0 && <p className="muted">{Object.entries(list.tiers).map(([t, n]) => `${t}: ${n}`).join(" · ")}</p>}
          {list.source && <p className="muted">Source: {list.source}</p>}
        </div>
        <div className="rule-actions">
          <Button variant="secondary" disabled={!list.loaded} onClick={() => setOpen(open === "browse" ? null : "browse")}>{open === "browse" ? "Hide entries" : "View entries"}</Button>
          <Button variant="secondary" onClick={() => setOpen(open === "edit" ? null : "edit")}>{open === "edit" ? "Close" : "Update list"}</Button>
        </div>
      </div>
      {open === "browse" && <Browse name={list.name} />}
      {open === "edit" && <Editor list={list} done={reload} />}
    </section>
  );
}

export default function Lists() {
  const { data, error, reload } = useLoad(api.referenceLists, []);
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Reference data</p>
          <h1>Reference lists</h1>
          <p>The official lists that rules check against. Update a list when a new edition is published.</p>
        </div>
      </div>
      <div className="notice"><Icon name="info" />
        Saving a list is recorded in the audit trail. Applications already assessed keep their results; re-assess one to check it against the new list.
        A course is matched to the occupation list by shared word stems, so the officer still confirms each result.
      </div>
      <ErrorNotice error={error} />
      {!data && !error && <Loading />}
      {data?.map((l) => <ListCard key={l.name} list={l} reload={reload} />)}
    </main>
  );
}
