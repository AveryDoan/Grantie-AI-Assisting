// Applicant application form, from the Figma Make design (ApplicantFlow.tsx),
// wired to the API: a draft is saved on the server, documents are really
// uploaded (text is read on the server, never by an AI), the check before
// submit is the server's pre-check, and submit records the person-only choice.
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { api, ApiError, type DraftCheck, type UploadResult } from "./api";
import { useAuth } from "./auth";
import studyNtLogo from "./assets/study-nt-logo.svg";

type Step = 1 | 2 | 3 | 4 | 5 | 6 | 7;
type UploadState = { state: "empty" | "uploading" | "uploaded" | "error"; name?: string; message?: string; tone?: "ok" | "info"; docId?: string };

const stepNames = ["Welcome and eligibility", "Individual details", "Study details", "Supporting documents", "Declaration", "Check before you submit", "Submitted"];
const providers = ["Australian City International College", "Alana Kaye College", "Alice Springs College of Australia", "Canterbury Institute of Management", "Charles Darwin University", "Darwin City College", "Flinders University", "Fox Education and Consultancy", "International College of Advanced Education", "Kormilda College Ltd", "Latitude College", "St John's Catholic College"];
const countries = ["Australia", "Bangladesh", "Brazil", "Canada", "China", "Colombia", "France", "Germany", "India", "Indonesia", "Japan", "Malaysia", "Nepal", "New Zealand", "Pakistan", "Philippines", "Singapore", "South Korea", "Sri Lanka", "Thailand", "United Kingdom", "United States", "Vietnam"];
const DRAFT_KEY = "study-nt-draft-id";

// Upload slot -> the document type the server and the rules understand.
const SLOT_TYPE: Record<string, string> = {
  coe: "coe", arrival: "travel booking", letter1: "referee letter", letter2: "referee letter", bio: "headshot", other: "other",
};
const LOOKS_LIKE: Record<string, string> = {
  coe: "a CoE", visa: "a visa notice", travel_booking: "a travel booking", flight_screenshot: "a flight screenshot",
  referee_letter: "a referee letter", headshot: "a headshot", offer_letter: "an offer letter", transcript: "a transcript",
  certified_translation: "a certified translation", travel_document: "a passport", other: "another kind of document",
};
const EXPECTED: Record<string, string> = { coe: "coe", arrival: "travel_booking", letter1: "referee_letter", letter2: "referee_letter" };

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    check: <path d="m5 12 4 4L19 6" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    left: <path d="m15 18-6-6 6-6" />,
    save: <><path d="M5 3h12l2 2v16H5z" /><path d="M8 3v6h8V3M8 21v-7h8v7" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
    warning: <><path d="M12 3 2.8 20h18.4z" /><path d="M12 9v4M12 17h.01" /></>,
    error: <><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6m0-6-6 6" /></>,
    upload: <><path d="M12 16V4m-5 5 5-5 5 5" /><path d="M5 20h14" /></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16h14V8z" /><path d="M14 2v6h6" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    person: <><circle cx="12" cy="8" r="4" /><path d="M4.5 21a7.5 7.5 0 0 1 15 0" /></>,
    flag: <><path d="M5 21V4m0 1h11l-2 4 2 4H5" /></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M5 21h14" /></>,
    lock: <><rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>,
    phone: <><path d="M6.6 10.8a15.5 15.5 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.2l3.2 1.1a1 1 0 0 1 .7 1v3a1 1 0 0 1-1 1A17.3 17.3 0 0 1 3 4.7a1 1 0 0 1 1-1h3a1 1 0 0 1 1 .7L9 7.6a1 1 0 0 1-.2 1z" /></>,
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  };
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function FlowButton({ children, variant = "primary", icon, onClick, type = "button", disabled }: { children: ReactNode; variant?: "primary" | "secondary" | "tertiary"; icon?: string; onClick?: () => void; type?: "button" | "submit"; disabled?: boolean }) {
  return <button className={`af-button af-button-${variant}`} type={type} onClick={onClick} disabled={disabled}>{icon && <Icon name={icon} />}{children}</button>;
}

function Required() {
  return <><span className="af-required" aria-hidden="true">*</span><span className="sr-only"> required</span></>;
}

function Field({ label, required, helper, error, children, className = "" }: { label: string; required?: boolean; helper?: string; error?: string; children: ReactNode; className?: string }) {
  return <div className={`af-field ${error ? "has-error" : ""} ${className}`}>
    <label>{label}{required && <Required />}{children}</label>
    {helper && !error && <small>{helper}</small>}
    {error && <span className="af-error"><Icon name="error" size={18} />{error}</span>}
  </div>;
}

function Notice({ type = "info", children }: { type?: "info" | "warning" | "error"; children: ReactNode }) {
  return <div className={`af-notice af-notice-${type}`} role={type === "error" ? "alert" : "note"}><Icon name={type === "warning" ? "warning" : type === "error" ? "error" : "info"} /><div>{children}</div></div>;
}

function Accordion({ title, children, defaultOpen = false }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return <section className="af-accordion">
    <button onClick={() => setOpen(!open)} aria-expanded={open}><span>{title}</span><span className={open ? "open" : ""}><Icon name="chevron" /></span></button>
    {open && <div className="af-accordion-body">{children}</div>}
  </section>;
}

function Toggle({ checked, onChange, label, note }: { checked: boolean; onChange: (value: boolean) => void; label: string; note?: string }) {
  return <label className="af-toggle-row"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="af-toggle" aria-hidden="true"><i /></span><span><strong>{label}</strong>{note && <small>{note}</small>}</span></label>;
}

function Stepper({ step }: { step: Step }) {
  return <nav className="af-stepper" aria-label="Application progress">
    <p>Step {step} of 7 · {stepNames[step - 1]}</p>
    <ol>{stepNames.map((name, index) => <li className={index + 1 < step ? "complete" : index + 1 === step ? "current" : ""} key={name} aria-current={index + 1 === step ? "step" : undefined}><span>{index + 1 < step ? <Icon name="check" size={16} /> : index + 1}</span><small>{name}</small></li>)}</ol>
  </nav>;
}

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("The file could not be read"));
    reader.readAsDataURL(file);
  });
}

function describe(slot: string, r: UploadResult): { message: string; tone: "ok" | "info" } {
  if (r.extraction_status !== "ok") {
    return { message: r.kind.startsWith("image") ? "Photo added. A person will check it." : "We could not read the text in this file. A person will check it.", tone: "info" };
  }
  const expected = EXPECTED[slot];
  if (expected && r.looks_like && r.looks_like !== expected) {
    return { message: `This looks like ${LOOKS_LIKE[r.looks_like] ?? "a different document"}, not ${LOOKS_LIKE[expected]}. Do you want to replace it?`, tone: "info" };
  }
  if (expected && r.looks_like === expected) return { message: `Looks like ${LOOKS_LIKE[expected]}.`, tone: "ok" };
  return { message: "File added. A person will check it.", tone: "ok" };
}

function UploadCard({ id, title, description, required, value, onFile, onRemove, checklist, allowComment, comment, setComment, disabled }: { id: string; title: string; description: string; required?: boolean; value: UploadState; onFile: (file: File) => void; onRemove: () => void; checklist?: string[]; allowComment?: boolean; comment?: string; setComment?: (value: string) => void; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"file" | "comment">("file");
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) onFile(file);
  };
  return <article className={`af-upload-card state-${value.state}`}>
    <div className="af-upload-heading"><span><Icon name="file" /></span><div><h3>{title}{required && <Required />}</h3><p>{description}</p></div></div>
    {allowComment && <div className="af-segmented" role="radiogroup" aria-label={`${title} response type`}><label><input type="radio" checked={mode === "file"} onChange={() => setMode("file")} />Upload a file</label><label><input type="radio" checked={mode === "comment"} onChange={() => setMode("comment")} />Comment in lieu of a file</label></div>}
    {mode === "comment" ? <Field label="Comment"><textarea rows={5} value={comment} onChange={(event) => setComment?.(event.target.value)} placeholder="Add your comment here" /></Field> :
      <div className="af-dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file && !disabled) onFile(file); }}>
        <input ref={input} id={`upload-${id}`} type="file" accept=".pdf,.txt,image/png,image/jpeg" onChange={choose} disabled={disabled} />
        {value.state === "empty" && <><Icon name="upload" size={28} /><strong>Upload a single file</strong><span>Click to select a file or drag to add a file</span><small>PDF, plain text, or a JPG/PNG photo · up to 5 MB</small><FlowButton variant="secondary" disabled={disabled} onClick={() => input.current?.click()}>Select file</FlowButton></>}
        {value.state === "uploading" && <><span className="af-spinner" /><strong>Uploading {value.name}</strong><span>Please wait while we add your file.</span></>}
        {value.state === "uploaded" && <><span className="af-file-success"><Icon name="check" /></span><strong>{value.name}</strong><span className="af-upload-status"><Icon name={value.tone === "ok" ? "check" : "info"} />{value.message}</span><div className="af-inline-actions"><button onClick={() => input.current?.click()}>Replace</button><button onClick={onRemove}>Remove</button></div></>}
        {value.state === "error" && <><span className="af-file-error"><Icon name="error" /></span><strong>{value.name}</strong><span className="af-error">{value.message}</span><FlowButton variant="secondary" onClick={() => input.current?.click()}>Choose another file</FlowButton></>}
      </div>}
    {checklist && <div className="af-document-checklist"><strong>Letter checklist</strong><ul>{checklist.map((item) => <li key={item}><Icon name="check" size={16} />{item}</li>)}</ul></div>}
  </article>;
}

function ApplicantHeader({ onHome, onResume }: { onHome: () => void; onResume: () => void }) {
  return <header className="af-header"><button className="af-brand" onClick={onHome}><img src={studyNtLogo} alt="Study NT" className="af-brand-logo" /><strong>Scholarship application</strong></button><div><button className="af-header-link" onClick={onResume}><Icon name="save" />Resume your application</button><a href="tel:1800193111"><Icon name="phone" />1800 193 111</a></div></header>;
}

function ApplicantFooter() {
  return <footer className="af-footer"><div><div><img src={studyNtLogo} alt="Study NT" className="af-footer-logo-img" /><p>Study in Australia's Northern Territory Scholarship</p></div><div><strong>Acknowledgement of Country</strong><p>Placeholder — approved wording to be supplied.</p></div><nav><button>Privacy</button><button>Accessibility</button><a href="tel:1800193111">1800 193 111</a></nav></div><p>Test system · A person makes every decision.</p></footer>;
}

// ---------------------------------------------------------------- sign-in (applicant role)

function ApplicantSignIn({ onHome }: { onHome: () => void }) {
  const { mode, me, error, loginDemo, loginPassword, logout } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setFailure(null);
    try { await fn(); } catch (e) { setFailure(e instanceof Error ? e.message : "Sign-in failed"); } finally { setBusy(false); }
  };
  return <div className="af-app"><ApplicantHeader onHome={onHome} onResume={() => undefined} />
    <main className="af-resume"><span><Icon name="lock" size={28} /></span><p className="eyebrow">Applicant sign-in</p><h1>Sign in to apply</h1>
      <p>Your answers and files are saved to your account so you can come back later.</p>
      {me && me.role !== "applicant" && <Notice type="warning"><strong>You are signed in as {me.role === "admin" ? "an administrator" : "an officer"}.</strong><p>Officers cannot submit applications for applicants. Sign out to continue as an applicant.</p><button className="af-text-button" onClick={logout}>Sign out</button></Notice>}
      {(failure || error) && <Notice type="error">{failure ?? error}</Notice>}
      {mode === "demo" && <FlowButton disabled={busy} icon="person" onClick={() => void run(async () => { logout(); await loginDemo("applicant"); })}>Continue as demo applicant</FlowButton>}
      {mode === "supabase" && <form className="af-signin" onSubmit={(e: FormEvent) => { e.preventDefault(); void run(() => loginPassword(email, password, ["applicant"])); }}>
        <Field label="Email" required><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Password" required><input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <FlowButton type="submit" disabled={busy}>Sign in <Icon name="arrow" /></FlowButton>
      </form>}
    </main><ApplicantFooter /></div>;
}

const eligibility = [
  "You have applied for and received an offer to study with an NT education provider. A Confirmation of Enrolment (CoE) must be provided.",
  "You have enrolled in a course that starts between 1 October 2026 and 31 December 2026.",
  "Your course leads to an occupation on the NT Skilled Occupation Priority List.",
  "You can show a record of academic excellence, leadership and/or community involvement.",
  "You meet the provider's academic and English entry requirements.",
  "You are applying at least 2 weeks before you arrive in Australia.",
  "You are living outside Australia when you apply.",
  "You are not already living in the NT.",
  "You are not studying with an NT provider when you apply.",
  "You have received a valid student visa (subclass 500).",
  "You do not hold another scholarship (except the CDU Global Merit Scholarship).",
  "You are not an Australian or New Zealand citizen, or an Australian permanent resident.",
  "You plan to study full-time.",
  "You will provide two recent referee letters, no older than 24 months where possible.",
  "You understand the obligations below.",
];

const declarationStatements = [
  "I understand the eligibility requirements for the Study in Australia's Northern Territory Scholarship Program and confirm I meet them.",
  "I agree to provide any supporting documents requested by Study NT.",
  "I understand that the scholarship is not transferable, not refundable and cannot be given to another person or provider.",
  "All information in the application, together with any attachments, is complete, true and correct to the best of my knowledge.",
  "I understand that giving false or incorrect information to get a benefit may be a criminal offence.",
  "I have read, understood and agree to comply with any requirements and conditions in the grant information and application form.",
  "I have read, understood and agree to the Privacy Statement below.",
  "To my knowledge, I do not have any conflict of interest to declare in relation to my application.",
  "I will tell the Department straight away if any information in the application changes.",
  "I understand that I may be asked to provide further information.",
  "If details of another person or organisation are in the application, I confirm they know and have given permission.",
  "I give consent for the NT Government to use my personal information in line with the Notice below and the Terms and Conditions of the program.",
];

const EMPTY_FORM = {
  title: "Ms", given: "", family: "", phone: "", email: "",
  street: "", suburb: "", region: "", country: "", postcode: "", samePostal: true,
  postalStreet: "", postalSuburb: "", postalCountry: "",
  nationality: "", citizen: "", under18: "", visa: "", provider: "Charles Darwin University", round: false,
  courseName: "", studyLoad: "", currentStudy: "", achievements: "", leadership: "", community: "",
  statement: "", arrival: "", contactName: "", contactPhone: "", contactEmail: "", studentContact: "",
  bioComment: "", otherComment: "", declarationName: "", declarationYes: false, declarationPlace: "",
  declarationTitle: "Ms", declarationGiven: "", declarationFamily: "", dob: "", declarationDate: new Date().toISOString().slice(0, 10),
};
type Form = typeof EMPTY_FORM;

// Form -> the field names the rules and the redaction config use. Data minimisation:
// only what a rule or a person needs is sent (gender is not collected).
function payload(form: Form): { fields: Record<string, string>; answers: Record<string, string> } {
  const join = (...parts: string[]) => parts.map((p) => p.trim()).filter(Boolean).join(", ");
  const residential = join(form.street, form.suburb, form.region, form.postcode, form.country);
  const postal = form.samePostal ? residential : join(form.postalStreet, form.postalSuburb, form.postalCountry);
  const name = `${form.given} ${form.family}`.trim();
  const fields: Record<string, string> = {
    applicant_name: name, given_name: form.given, family_name: form.family, title: form.title,
    date_of_birth: form.dob, email: form.email, phone: form.phone, nationality: form.nationality,
    australian_or_nz_citizen_or_pr: form.citizen, residential_address: residential, residential_country: form.country,
    postal_address: postal, postal_country: form.samePostal ? form.country : form.postalCountry,
    education_provider: form.provider, course_name: form.courseName, study_load: form.studyLoad,
    higher_education_round_1: form.round ? "Yes" : "", arrival_date: form.arrival, under_18: form.under18,
    student_visa_500: form.visa, primary_contact_is_student: form.studentContact,
    contact_name: form.studentContact === "No" ? form.contactName : "", contact_phone: form.studentContact === "No" ? form.contactPhone : "",
    contact_email: form.studentContact === "No" ? form.contactEmail : "",
    declaration_agreed: form.declarationYes ? "Yes" : "", declaration_name: form.declarationName,
    declaration_place: form.declarationPlace, declaration_date: form.declarationDate,
  };
  const answers: Record<string, string> = {
    current_study: form.currentStudy, academic_achievements: form.achievements, leadership: form.leadership,
    community_engagement: form.community, nt_contribution: form.statement, biography: form.bioComment,
    other_supporting_information: form.otherComment,
  };
  const clean = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v && v.trim()));
  return { fields: clean(fields), answers: clean(answers) };
}

function formFrom(fields: Record<string, string> = {}, answers: Record<string, string> = {}): Form {
  return {
    ...EMPTY_FORM,
    title: fields.title ?? EMPTY_FORM.title, given: fields.given_name ?? "", family: fields.family_name ?? "",
    phone: fields.phone ?? "", email: fields.email ?? "", street: fields.residential_address ?? "", country: fields.residential_country ?? "",
    nationality: fields.nationality ?? "", citizen: fields.australian_or_nz_citizen_or_pr ?? "", under18: fields.under_18 ?? "",
    visa: fields.student_visa_500 ?? "", provider: fields.education_provider ?? EMPTY_FORM.provider, round: fields.higher_education_round_1 === "Yes",
    courseName: fields.course_name ?? "", studyLoad: fields.study_load ?? "", arrival: fields.arrival_date ?? "",
    studentContact: fields.primary_contact_is_student ?? "", contactName: fields.contact_name ?? "", contactPhone: fields.contact_phone ?? "",
    contactEmail: fields.contact_email ?? "", declarationYes: fields.declaration_agreed === "Yes", declarationName: fields.declaration_name ?? "",
    declarationPlace: fields.declaration_place ?? "", declarationDate: fields.declaration_date ?? EMPTY_FORM.declarationDate, dob: fields.date_of_birth ?? "",
    declarationGiven: fields.given_name ?? "", declarationFamily: fields.family_name ?? "",
    currentStudy: answers.current_study ?? "", achievements: answers.academic_achievements ?? "", leadership: answers.leadership ?? "",
    community: answers.community_engagement ?? "", statement: answers.nt_contribution ?? "", bioComment: answers.biography ?? "",
    otherComment: answers.other_supporting_information ?? "",
  };
}

function message(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong. Please try again.";
}

export default function ApplicantFlow({ onHome }: { onHome: () => void }) {
  const { me } = useAuth();
  if (!me || me.role !== "applicant") return <ApplicantSignIn onHome={onHome} />;
  return <SignedInFlow onHome={onHome} />;
}

function SignedInFlow({ onHome }: { onHome: () => void }) {
  const [step, setStep] = useState<Step>(1);
  const [resume, setResume] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [offline, setOffline] = useState(!navigator.onLine);
  const [personOnly, setPersonOnly] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [draftId, setDraftId] = useState<string | null>(() => { try { return sessionStorage.getItem(DRAFT_KEY); } catch { return null; } });
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Record<string, UploadState>>({ coe: { state: "empty" }, arrival: { state: "empty" }, letter1: { state: "empty" }, letter2: { state: "empty" }, bio: { state: "empty" }, other: { state: "empty" } });
  const [extraUploads, setExtraUploads] = useState(0);
  const [check, setCheck] = useState<DraftCheck | null>(null);
  const [submitted, setSubmitted] = useState<{ reference: string } | null>(null);

  useEffect(() => {
    const online = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", online); window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", online); window.removeEventListener("offline", off); };
  }, []);

  // Reload a saved draft (answers and the files already uploaded).
  useEffect(() => {
    if (!draftId) return;
    api.applicantDetail(draftId).then((d) => {
      if (d.application.status !== "draft") { forget(); return; }
      setForm(formFrom(d.application.application_text.fields, d.application.application_text.answers));
      const slots: Record<string, UploadState> = {};
      const byType: Record<string, string[]> = { coe: ["coe"], "travel booking": ["arrival"], "referee letter": ["letter1", "letter2"], headshot: ["bio"], other: ["other"] };
      for (const doc of d.documents) {
        const slot = (byType[doc.declared_type] ?? []).find((s) => !slots[s]);
        if (slot) slots[slot] = { state: "uploaded", name: doc.file_name, message: "File added earlier.", tone: "ok", docId: doc.id };
      }
      setUploads((u) => ({ ...u, ...slots }));
    }).catch(() => forget());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const forget = () => { setDraftId(null); try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ } };
  const remember = (id: string) => { setDraftId(id); try { sessionStorage.setItem(DRAFT_KEY, id); } catch { /* ignore */ } };

  const update = (key: string, value: string | boolean) => {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => { const next = { ...current }; delete next[key]; return next; });
  };
  const flash = (text: string) => { setToast(text); window.setTimeout(() => setToast(null), 2800); };

  // Create the draft on first save, then keep it in step with the form.
  const persist = async (): Promise<string> => {
    const body = payload(form);
    if (draftId) { await api.saveDraft(draftId, body); return draftId; }
    const programs = await api.programs();
    const program = programs.find((p) => /study/i.test(p.name)) ?? programs[0];
    const created = await api.createDraft({ grant_program_id: program?.id, ...body });
    remember(created.id);
    return created.id;
  };
  const save = async () => {
    setBusy(true); setFailure(null);
    try { await persist(); flash("Saved."); } catch (e) { setFailure(message(e)); } finally { setBusy(false); }
  };

  const go = (next: Step) => { setStep(next); setResume(false); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const next = async () => {
    const nextErrors: Record<string, string> = {};
    if (step === 2) {
      if (!form.given) nextErrors.given = "Enter your given name.";
      if (!form.family) nextErrors.family = "Enter your family name.";
      if (!form.email.includes("@")) nextErrors.email = "Enter an email address in the format name@example.com.";
      if (!form.country) nextErrors.country = "Enter the country where you live.";
      if (!form.citizen) nextErrors.citizen = "Choose an option.";
      if (!form.visa) nextErrors.visa = "Tell us whether you have a valid student visa.";
    }
    if (step === 3) {
      if (!form.round) nextErrors.round = "Confirm that your course is in Higher Education Round 1.";
      if (!form.courseName.trim()) nextErrors.courseName = "Enter the name of your course.";
      if (!form.statement.trim()) nextErrors.statement = "Tell us how studying in the NT will contribute to your future and the Northern Territory.";
      if (!form.studentContact) nextErrors.studentContact = "Tell us whether the primary contact is the student.";
    }
    if (step === 5 && !form.declarationYes) nextErrors.declarationYes = "You must confirm the declaration before continuing.";
    if (Object.keys(nextErrors).length) { setErrors(nextErrors); window.setTimeout(() => document.querySelector(".has-error")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0); return; }
    if (step >= 2) {
      setBusy(true); setFailure(null);
      try {
        const id = await persist();
        if (step === 5) setCheck(await api.checkDraft(id));
      } catch (e) { setFailure(message(e)); setBusy(false); return; }
      setBusy(false);
    }
    go(Math.min(7, step + 1) as Step);
  };
  const previous = () => go(Math.max(1, step - 1) as Step);

  const uploadFile = async (slot: string, file: File) => {
    if (file.size > 5 * 1024 * 1024) {
      setUploads((u) => ({ ...u, [slot]: { state: "error", name: file.name, message: "File too large. Choose a file smaller than 5 MB or ask a person for help." } }));
      return;
    }
    setUploads((u) => ({ ...u, [slot]: { state: "uploading", name: file.name } }));
    try {
      const id = await persist();
      const old = uploads[slot]?.docId;
      if (old) await api.removeDocument(id, old).catch(() => undefined);
      const r = await api.uploadDocument(id, { file_name: file.name, declared_type: SLOT_TYPE[slot] ?? "other", content_base64: await toBase64(file) });
      setUploads((u) => ({ ...u, [slot]: { state: "uploaded", name: r.file_name, docId: r.id, ...describe(slot, r) } }));
    } catch (e) {
      setUploads((u) => ({ ...u, [slot]: { state: "error", name: file.name, message: message(e) } }));
    }
  };
  const removeFile = async (slot: string) => {
    const docId = uploads[slot]?.docId;
    if (draftId && docId) await api.removeDocument(draftId, docId).catch(() => undefined);
    setUploads((u) => ({ ...u, [slot]: { state: "empty" } }));
  };

  const submit = async () => {
    setBusy(true); setFailure(null);
    try {
      const id = await persist();
      const r = await api.submitDraft(id, personOnly);
      setSubmitted({ reference: r.reference });
      forget();
      go(7);
    } catch (e) { setFailure(message(e)); } finally { setBusy(false); }
  };

  const residentialWarning = /australia|northern territory|\bnt\b/i.test(`${form.street} ${form.suburb} ${form.region} ${form.country}`);
  const twoWeeks = new Date(Date.now() + 14 * 86400000).toISOString().slice(0, 10);
  const arrivalWarning = Boolean(form.arrival && form.arrival < twoWeeks);

  if (resume) return <div className="af-app"><ApplicantHeader onHome={onHome} onResume={() => setResume(false)} /><main className="af-resume"><span><Icon name="save" size={28} /></span><p className="eyebrow">Saved application</p><h1>Resume your application</h1><p>{draftId ? "Your draft is saved on our system." : "You have no saved draft in this browser session yet."}</p><div className="af-resume-card"><div><strong>Study in Australia's Northern Territory Scholarship</strong><span>Step {step} of 7 · {stepNames[step - 1]}</span></div><FlowButton onClick={() => setResume(false)}>Continue application <Icon name="arrow" /></FlowButton></div><button className="af-text-button" onClick={() => { forget(); setForm(EMPTY_FORM); setUploads({ coe: { state: "empty" }, arrival: { state: "empty" }, letter1: { state: "empty" }, letter2: { state: "empty" }, bio: { state: "empty" }, other: { state: "empty" } }); setStep(1); setResume(false); }}>Start a new application</button></main><ApplicantFooter /></div>;

  return <div className="af-app">
    <ApplicantHeader onHome={onHome} onResume={() => setResume(true)} />
    {offline && <div className="af-offline" role="alert"><Icon name="warning" />You are offline. You can keep typing, but uploads and saving may not work until you reconnect.</div>}
    {toast && <div className="af-toast" role="status"><Icon name="check" /><span><strong>{toast}</strong> You can finish later.</span></div>}
    <main className="af-main">
      <Stepper step={step} />
      {failure && <Notice type="error">{failure}</Notice>}
      {step === 1 && <WelcomeStep personOnly={personOnly} setPersonOnly={setPersonOnly} />}
      {step === 2 && <IndividualStep form={form} update={update} errors={errors} residentialWarning={residentialWarning} />}
      {step === 3 && <StudyStep form={form} update={update} errors={errors} arrivalWarning={arrivalWarning} />}
      {step === 4 && <DocumentsStep uploads={uploads} onFile={(s, f) => void uploadFile(s, f)} onRemove={(s) => void removeFile(s)} form={form} update={update} extraUploads={extraUploads} setExtraUploads={setExtraUploads} setUploads={setUploads} />}
      {step === 5 && <DeclarationStep form={form} update={update} errors={errors} />}
      {step === 6 && <CheckStep go={go} check={check} uploads={uploads} form={form} arrivalWarning={arrivalWarning} personOnly={personOnly} setPersonOnly={setPersonOnly} submit={() => void submit()} busy={busy} />}
      {step === 7 && <SubmittedStep onHome={onHome} name={`${form.given} ${form.family}`.trim()} reference={submitted?.reference ?? ""} personOnly={personOnly} />}
      {step < 7 && <div className="af-navigation">
        <div>{step > 1 && <FlowButton variant="secondary" icon="left" onClick={previous}>Previous</FlowButton>}<FlowButton variant="tertiary" icon="save" disabled={busy} onClick={() => void save()}>Save and continue later</FlowButton></div>
        {step === 1 ? <FlowButton onClick={() => go(2)}>Start application <Icon name="arrow" /></FlowButton> : step === 6 ? <FlowButton disabled={busy} onClick={() => void submit()}>Submit application <Icon name="arrow" /></FlowButton> : <FlowButton disabled={busy} onClick={() => void next()}>{busy ? "Saving…" : "Next"} <Icon name="arrow" /></FlowButton>}
      </div>}
    </main>
    <ApplicantFooter />
  </div>;
}

function WelcomeStep({ personOnly, setPersonOnly }: { personOnly: boolean; setPersonOnly: (value: boolean) => void }) {
  return <div className="af-step">
    <header className="af-step-title"><p className="eyebrow">Higher Education · Round 1</p><h1>Study in Australia's Northern Territory Scholarship</h1><p>The Northern Territory Government provides these scholarships to support talented international students who want to study in the NT. Scholarships recognise strong academic results, leadership qualities and community involvement.</p></header>
    <Notice type="warning"><strong>This is a test system.</strong><p>Only enter information and upload documents that you are allowed to use for testing. Before any AI reads your application, names, contact details, addresses, dates of birth and ID numbers are replaced with placeholders on our server. Files that cannot be read as text (scans, photos) are never sent to the AI; a person checks them.</p></Notice>
    <section className="af-card af-eligibility"><div className="af-card-heading"><span><Icon name="check" /></span><div><h2>In making this application you are saying that:</h2><p>Read each statement before you start.</p></div></div><ul>{eligibility.map((item) => <li key={item}><Icon name="check" />{item}</li>)}</ul></section>
    <div className="af-accordions">
      <Accordion title="How we assess applications"><div className="af-table-wrap"><table><thead><tr><th>Area</th><th>Weight</th><th>What to provide</th></tr></thead><tbody><tr><td><strong>Academic Merit</strong></td><td>40%</td><td>Grades and achievements. Please provide transcripts.</td></tr><tr><td><strong>Supporting Evidence</strong></td><td>30%</td><td>References on business letterhead, signed and dated, from 2024 to 2026.</td></tr><tr><td><strong>Leadership</strong></td><td>20%</td><td>Roles, initiatives and impact.</td></tr><tr><td><strong>Community Engagement</strong></td><td>10%</td><td>Volunteering or contribution to community.</td></tr></tbody></table></div></Accordion>
      <Accordion title="If you receive the scholarship"><ul className="af-bullet-list"><li>Study full-time and keep satisfactory progress.</li><li>Pay any tuition fees not covered.</li><li>Follow the student code of conduct.</li><li>Tell Study NT and your provider straight away if your visa status changes.</li><li>Attend the Study NT Welcome Reception in Darwin in March 2027 where possible.</li><li>The scholarship cannot be transferred, refunded or converted to cash.</li></ul></Accordion>
      <Accordion title="How we use AI" defaultOpen><p>A person makes every decision. AI helps staff check your documents and gives you clearer reasons.</p><Toggle checked={personOnly} onChange={setPersonOnly} label="I would like a person-only assessment" note="This will not affect your chances." /></Accordion>
    </div>
    <Notice type="warning"><strong>Please read each question carefully and answer truthfully.</strong><p>If you are successful, this form and your declaration become a legally binding contract between you and the Northern Territory Government. Successful applicants will be told by letter.</p></Notice>
    <Notice><strong>You can receive funding from only one program.</strong><p>You can receive either the 2026/27 International Student Accommodation Grant or the Study in Australia's NT Scholarship, not both.</p></Notice>
  </div>;
}

type StepProps = { form: Form; update: (key: string, value: string | boolean) => void; errors: Record<string, string> };

function Radios({ name, value, options, update, legend, error, children }: { name: keyof Form; value: string; options: string[]; update: StepProps["update"]; legend: ReactNode; error?: string; children?: ReactNode }) {
  return <fieldset className={`af-choice-group ${error ? "has-error" : ""}`}><legend>{legend}</legend>{children}{options.map((x) => <label key={x}><input type="radio" name={name} checked={value === x} onChange={() => update(name, x)} />{x}</label>)}{error && <span className="af-error"><Icon name="error" size={18} />{error}</span>}</fieldset>;
}

function IndividualStep({ form, update, errors, residentialWarning }: StepProps & { residentialWarning: boolean }) {
  return <div className="af-step"><header className="af-step-title"><p className="eyebrow">Step 2</p><h1>Individual details</h1><p>Tell us about yourself and how we can contact you.</p></header>
    <section className="af-card"><h2>Your name and contact details</h2><div className="af-form-grid">
      <Field label="Title" required><select value={form.title} onChange={(e) => update("title", e.target.value)}>{["Ms", "Mr", "Mx", "Dr", "Other"].map((x) => <option key={x}>{x}</option>)}</select></Field>
      <Field label="Given name" required error={errors.given}><input value={form.given} autoComplete="given-name" onChange={(e) => update("given", e.target.value)} /></Field>
      <Field label="Family name" required error={errors.family}><input value={form.family} autoComplete="family-name" onChange={(e) => update("family", e.target.value)} /></Field>
      <Field label="Contact phone number" helper="Include your country code, for example +60 12 345 6789." className="span-2"><input value={form.phone} autoComplete="tel" onChange={(e) => update("phone", e.target.value)} /></Field>
      <Field label="Email" required error={errors.email} className="span-2"><input type="email" autoComplete="email" value={form.email} onChange={(e) => update("email", e.target.value)} /></Field>
    </div></section>
    <section className="af-card"><h2>Residential address</h2><div className="af-form-grid">
      <Field label="Street address" required className="span-2"><input value={form.street} autoComplete="street-address" onChange={(e) => update("street", e.target.value)} /></Field>
      <Field label="Suburb or city" required><input value={form.suburb} onChange={(e) => update("suburb", e.target.value)} /></Field><Field label="State or region" required><input value={form.region} onChange={(e) => update("region", e.target.value)} /></Field>
      <Field label="Country" required error={errors.country}><input list="countries" value={form.country} onChange={(e) => update("country", e.target.value)} /><datalist id="countries">{countries.map((x) => <option key={x} value={x} />)}</datalist></Field><Field label="Postcode" required><input value={form.postcode} onChange={(e) => update("postcode", e.target.value)} /></Field>
    </div>{residentialWarning && <Notice><strong>This scholarship is for students living outside Australia.</strong><p>Check your address or ask a person for help. You can still continue.</p><button className="af-text-button" onClick={() => { window.location.href = "tel:1800193111"; }}>Ask a person for help</button></Notice>}</section>
    <section className="af-card"><h2>Postal address</h2><label className="af-check"><input type="checkbox" checked={form.samePostal} onChange={(e) => update("samePostal", e.target.checked)} /><span>Same as residential address</span></label>{!form.samePostal && <div className="af-form-grid af-revealed"><Field label="Postal street address" required className="span-2"><input value={form.postalStreet} onChange={(e) => update("postalStreet", e.target.value)} /></Field><Field label="Postal suburb or city" required><input value={form.postalSuburb} onChange={(e) => update("postalSuburb", e.target.value)} /></Field><Field label="Postal country" required><input list="countries" value={form.postalCountry} onChange={(e) => update("postalCountry", e.target.value)} /></Field></div>}</section>
    <section className="af-card"><h2>About you</h2><Field label="Nationality" required><input list="countries" value={form.nationality} onChange={(e) => update("nationality", e.target.value)} /></Field>
      <Radios name="citizen" value={form.citizen} options={["No", "Yes"]} update={update} error={errors.citizen} legend={<>Are you an Australian or New Zealand citizen, or an Australian permanent resident? <Required /></>} />
      <Radios name="under18" value={form.under18} options={["Yes", "No"]} update={update} legend="Are you under 18 years of age at the time you are submitting this application?" />
      {form.under18 === "Yes" && <Notice><strong>We may need extra information.</strong><p>A person from Study NT will contact you. You can still continue.</p></Notice>}
      <Radios name="visa" value={form.visa} options={["No", "Yes"]} update={update} error={errors.visa} legend={<>Do you have a valid student visa (subclass 500)? <Required /></>}><p>You must have been granted a student visa 500 to receive the Study in Australia's Northern Territory Scholarship.</p></Radios>
    </section>
  </div>;
}

function StudyStep({ form, update, errors, arrivalWarning }: StepProps & { arrivalWarning: boolean }) {
  const fillContact = () => { update("contactName", `${form.given} ${form.family}`); update("contactPhone", form.phone); update("contactEmail", form.email); };
  const text = (key: keyof Form, label: string, helper: string, rows = 5, required = false) => (
    <Field label={label} helper={helper} required={required} error={errors[key]}><textarea rows={rows} maxLength={4000} value={String(form[key])} onChange={(e) => update(key, e.target.value)} /><span className="af-counter" aria-live="polite">{String(form[key]).length} / 4000</span></Field>
  );
  return <div className="af-step"><header className="af-step-title"><p className="eyebrow">Step 3</p><h1>Study details</h1><p>Tell us where, when and why you plan to study in the Northern Territory.</p></header>
    <Notice><strong>Higher education applications are assessed in two rounds.</strong><p>You can submit only one application for a higher education course. The date you submit decides the round. If your course starts before 31 December 2026, apply in Round 1. Courses starting between 1 January and 1 May 2027 apply in Round 2, which opens 1 January 2027.</p><div className="af-rounds"><span><strong>Round 1</strong>1 October 2026 to 31 December 2026</span><span><strong>Round 2</strong>1 January 2027 to 31 May 2027</span></div></Notice>
    <section className="af-card"><h2>Your course</h2><Field label="Education provider" required><select value={form.provider} onChange={(e) => update("provider", e.target.value)}>{providers.map((x) => <option key={x}>{x}</option>)}</select></Field>
      <div className="af-form-grid"><Field label="Course name" required error={errors.courseName} helper="As it appears on your CoE, for example Bachelor of Nursing."><input value={form.courseName} onChange={(e) => update("courseName", e.target.value)} /></Field>
        <Field label="Study load" required><select value={form.studyLoad} onChange={(e) => update("studyLoad", e.target.value)}><option value="">Choose</option><option>Full-time</option><option>Part-time</option></select></Field></div>
      <div className={errors.round ? "af-check has-error" : "af-check"}><label><input type="checkbox" checked={form.round} onChange={(e) => update("round", e.target.checked)} /><span><strong>Higher Education Round 1: my course starts between 1 October 2026 and 31 December 2026</strong> <Required /></span></label>{errors.round && <span className="af-error"><Icon name="error" size={18} />{errors.round}</span>}<small>If you are applying for ELICOS, secondary school or VET/TAFE, you are in the wrong application. <a href="#other-program">Go to the 2026/27 Scholarship Program: Study in Australia's Northern Territory</a>.</small></div>
    </section>
    <section className="af-card"><h2>Your own words</h2><Notice type="warning"><strong>Please use your own words.</strong><p>Use AI only for basic tasks like spelling, grammar and organising ideas. Do not use AI to write, improve or paraphrase any part of your application.</p></Notice><p className="af-reassurance"><Icon name="person" />We value your own voice. Plain English is fine.</p>
      {text("currentStudy", "What are you studying now, and where?", "For example: final year of high school in your home country.", 3)}
      {text("achievements", "Academic achievements", "Grades, prizes or results you are proud of.")}
      {text("leadership", "Leadership", "Roles you have taken on, what you started and what changed.")}
      {text("community", "Community engagement", "Volunteering or other ways you contribute to your community.")}
      {text("statement", "How will studying in the NT contribute to your future and the Northern Territory?", "", 10, true)}
    </section>
    <section className="af-card"><h2>Arrival and contact</h2><Field label="Date of arrival in the NT" required helper="You cannot apply for this scholarship if you are already living in Australia."><input type="date" value={form.arrival} onChange={(e) => update("arrival", e.target.value)} /></Field>{arrivalWarning && <Notice type="warning"><strong>Applications must be submitted at least 2 weeks before you arrive in Australia.</strong><p>Check your date or ask a person for help. You can still continue.</p></Notice>}
      <Radios name="studentContact" value={form.studentContact} options={["Yes", "No"]} update={update} error={errors.studentContact} legend={<>Is the primary contact the student? <Required /></>} />
      {form.studentContact === "No" && <><h3>Who will be the primary contact for this application?</h3><div className="af-form-grid"><Field label="Contact name" required><input value={form.contactName} onChange={(e) => update("contactName", e.target.value)} /></Field><Field label="Phone number" required><input value={form.contactPhone} onChange={(e) => update("contactPhone", e.target.value)} /></Field><Field label="Email address" required className="span-2"><input type="email" value={form.contactEmail} onChange={(e) => update("contactEmail", e.target.value)} /></Field></div></>}
      {form.studentContact === "Yes" && <button className="af-text-button" onClick={fillContact}>We will use the contact details from Individual details</button>}
    </section>
  </div>;
}

function DocumentsStep({ uploads, onFile, onRemove, form, update, extraUploads, setExtraUploads, setUploads }: { uploads: Record<string, UploadState>; onFile: (slot: string, file: File) => void; onRemove: (slot: string) => void; form: Form; update: StepProps["update"]; extraUploads: number; setExtraUploads: (value: number) => void; setUploads: (fn: (u: Record<string, UploadState>) => Record<string, UploadState>) => void }) {
  const checklist = ["Referee name", "Position", "Organisation", "Relationship", "Length of association", "Signature", "Company letterhead", "Dated 2024 to 2026"];
  const letterDescription = "Provide one referee letter from an appropriate person (for example an employer, teacher or lecturer). The letter should give an honest, evidence-based assessment of your academic, personal and leadership qualities. Include the referee's name, position, organisation, signature, relationship to you and length of association. The letter must be on company letterhead, signed and dated.";
  const card = (slot: string, props: Omit<Parameters<typeof UploadCard>[0], "id" | "value" | "onFile" | "onRemove">) =>
    <UploadCard id={slot} value={uploads[slot] ?? { state: "empty" }} onFile={(f) => onFile(slot, f)} onRemove={() => onRemove(slot)} {...props} />;
  return <div className="af-step"><header className="af-step-title"><p className="eyebrow">Step 4</p><h1>Supporting documents</h1><p>All documents must be in English. Documents in another language need a certified English translation, plus a copy of the original.</p></header>
    <Notice><strong>Use text-based PDFs where you can.</strong><p>We read the text in each file to check it. Scanned pages and photos cannot be read automatically, so a person checks them, which can take longer.</p></Notice>
    <div className="af-upload-list">
      {card("coe", { title: "Confirmation of Enrolment (CoE)", description: "Please attach your Confirmation of Enrolment document.", required: true })}
      {card("arrival", { title: "Evidence of arrival date in Australia", description: "For example a booking confirmation for an airline ticket, a travel itinerary or similar. A screenshot of your intended flight is not enough.", required: true })}
      {card("letter1", { title: "Letter of Support #1", description: letterDescription, required: true, checklist })}
      {card("letter2", { title: "Letter of Support #2", description: letterDescription, required: true, checklist })}
      {card("bio", { title: "Biography (BIO) and headshot", description: "Please write a 150 word BIO of yourself and upload a headshot photo.", allowComment: true, comment: form.bioComment, setComment: (v) => update("bioComment", v) })}
      {card("other", { title: "Other supporting documents (optional)", description: "For example a resume, certificates, awards or academic achievements.", allowComment: true, comment: form.otherComment, setComment: (v) => update("otherComment", v) })}
      {Array.from({ length: extraUploads }, (_, index) => <div key={index}>{card(`other-${index}`, { title: `Other supporting document ${index + 2}`, description: "Add another optional file." })}</div>)}
      <FlowButton variant="secondary" onClick={() => { setUploads((u) => ({ ...u, [`other-${extraUploads}`]: { state: "empty" } })); setExtraUploads(extraUploads + 1); }}>Add another answer</FlowButton>
    </div>
  </div>;
}

function DeclarationStep({ form, update, errors }: StepProps) {
  return <div className="af-step"><header className="af-step-title"><p className="eyebrow">Step 5</p><h1>Unattested Declaration under the Oaths, Affidavits and Declarations Act</h1><p>Read every statement before you agree to the declaration.</p></header>
    <section className="af-card af-declaration"><p className="af-declaration-lead">I <label><span className="sr-only">Full name</span><input value={form.declarationName} onChange={(e) => update("declarationName", e.target.value)} aria-label="Full name" placeholder="Your full name" /></label> solemnly and sincerely declare that everything in this application form is true.</p><ol>{declarationStatements.map((item) => <li key={item}>{item}</li>)}</ol>
      <p><strong>This declaration is true and I know that it is an offence to make a declaration that is false in any material particular.</strong></p><div className={errors.declarationYes ? "af-check has-error" : "af-check"}><label><input type="checkbox" checked={form.declarationYes} onChange={(e) => update("declarationYes", e.target.checked)} /><span>Yes <Required /></span></label>{errors.declarationYes && <span className="af-error"><Icon name="error" size={18} />{errors.declarationYes}</span>}</div>
      <div className="af-form-grid af-declaration-fields"><Field label="This declaration is made at" required className="span-2"><input value={form.declarationPlace} onChange={(e) => update("declarationPlace", e.target.value)} /></Field><Field label="Title" required><select value={form.declarationTitle} onChange={(e) => update("declarationTitle", e.target.value)}>{["Ms", "Mr", "Mx", "Dr", "Other"].map((x) => <option key={x}>{x}</option>)}</select></Field><Field label="Given name" required><input value={form.declarationGiven || form.given} onChange={(e) => update("declarationGiven", e.target.value)} /></Field><Field label="Family name" required><input value={form.declarationFamily || form.family} onChange={(e) => update("declarationFamily", e.target.value)} /></Field><Field label="Date of birth" required><input type="date" value={form.dob} onChange={(e) => update("dob", e.target.value)} /></Field><Field label="Date of declaration" required><input type="date" value={form.declarationDate} onChange={(e) => update("declarationDate", e.target.value)} /></Field></div>
    </section>
    <section className="af-card af-privacy"><div className="af-card-heading"><span><Icon name="lock" /></span><div><h2>Collection and use of your information</h2></div></div><p>The information is needed to decide your suitability for NT Government grant funding. If you do not give the details, we may not be able to process your application.</p><p>If successful, the NT Government will make details of the funding you receive public. By submitting you consent to your information being used for this purpose and shared with other NT Government agencies to assess your application, for compliance, to process payments and for the general administration of grants.</p><p>You can access, correct and update your information by calling <a href="tel:1800193111">1800 193 111</a>.</p></section>
  </div>;
}

type Row = { area: string; status: "Done" | "Please check" | "Needs attention"; message: string; target: Step; icon: string };

function CheckStep({ go, check, uploads, form, arrivalWarning, personOnly, setPersonOnly, submit, busy }: { go: (step: Step) => void; check: DraftCheck | null; uploads: Record<string, UploadState>; form: Form; arrivalWarning: boolean; personOnly: boolean; setPersonOnly: (value: boolean) => void; submit: () => void; busy: boolean }) {
  const rows: Row[] = [];
  const individual = (check?.missing_fields ?? []).filter((m) => /citizen|residential|under_18|address|country/.test(m.field));
  const study = (check?.missing_fields ?? []).filter((m) => !individual.includes(m) && !m.field.startsWith("declaration") && m.field !== "biography");
  rows.push(individual.length ? { area: "Individual details", status: "Needs attention", message: `Missing: ${individual.map((m) => m.label).join(", ")}.`, target: 2, icon: "file" }
    : { area: "Individual details", status: "Done", message: "Your contact and address details are complete.", target: 2, icon: "check" });
  if (study.length) rows.push({ area: "Study details", status: "Needs attention", message: `Missing: ${study.map((m) => m.label).join(", ")}.`, target: 3, icon: "file" });
  else if (arrivalWarning) rows.push({ area: "Study details", status: "Please check", message: "Your arrival date is less than 2 weeks away.", target: 3, icon: "flag" });
  else rows.push({ area: "Study details", status: "Done", message: "Your course and study details are complete.", target: 3, icon: "check" });
  for (const m of check?.missing_documents ?? []) {
    rows.push({ area: "Documents", status: "Needs attention", message: `${m.label[0].toUpperCase()}${m.label.slice(1)}: ${m.provided} of ${m.needed} provided.`, target: 4, icon: "file" });
  }
  for (const w of check?.wrong_document_type ?? []) rows.push({ area: "Documents", status: "Please check", message: w.message, target: 4, icon: "flag" });
  const unread = Object.values(uploads).filter((u) => u.state === "uploaded" && u.tone === "info");
  if (unread.length) rows.push({ area: "Documents", status: "Please check", message: `${unread.length} file${unread.length > 1 ? "s" : ""} could not be read automatically or may be the wrong type. A person will check ${unread.length > 1 ? "them" : "it"}.`, target: 4, icon: "flag" });
  if (!(check?.missing_documents.length) && !(check?.wrong_document_type.length) && !unread.length) rows.push({ area: "Documents", status: "Done", message: "Your documents are attached.", target: 4, icon: "check" });
  rows.push(form.declarationYes ? { area: "Declaration", status: "Done", message: "You confirmed the declaration.", target: 5, icon: "check" }
    : { area: "Declaration", status: "Needs attention", message: "Confirm the declaration.", target: 5, icon: "file" });
  const anything = rows.some((r) => r.status !== "Done");
  return <div className="af-step"><header className="af-step-title"><p className="eyebrow">Step 6</p><h1>Check your application before you submit</h1><p>{anything ? "We found a few things worth checking. You can fix them now or submit your application anyway." : "Everything we check for is there."} A person will assess your application. {check?.note}</p></header>
    <section className="af-review-list" aria-label="Application check results">{rows.map((r, index) => <article key={`${r.area}-${index}`} className={`af-review-row status-${r.status.toLowerCase().replace(" ", "-")}`}><span className="af-review-icon"><Icon name={r.icon} /></span><div><span className="af-review-status"><Icon name={r.icon} size={16} />{r.status}</span><h2>{r.area}</h2><p>{r.message}</p></div><button onClick={() => go(r.target)}>{r.status === "Done" ? "Review" : "Fix this"} <Icon name="arrow" /></button></article>)}</section>
    <section className="af-card af-ai-panel"><div className="af-card-heading"><span><Icon name="person" /></span><div><h2>How we use AI</h2><p>AI helps staff check your documents and give clearer reasons. It does not decide whether you receive a scholarship. It only ever sees your application with personal details replaced by placeholders.</p></div></div><Toggle checked={personOnly} onChange={setPersonOnly} label="I would like a person-only assessment" note="This will not affect your chances." /></section>
    <div className="af-submit-options"><FlowButton variant="secondary" onClick={() => go(2)}>Go back and fix</FlowButton><FlowButton variant="secondary" icon="person" onClick={() => { window.location.href = "tel:1800193111"; }}>Ask a person for help</FlowButton><FlowButton disabled={busy} onClick={submit}>{anything ? "Submit anyway" : "Submit application"} <Icon name="arrow" /></FlowButton></div>
  </div>;
}

function SubmittedStep({ onHome, name, reference, personOnly }: { onHome: () => void; name: string; reference: string; personOnly: boolean }) {
  return <div className="af-step af-submitted"><span className="af-success-mark"><Icon name="check" size={34} /></span><p className="eyebrow">Application submitted</p><h1>Thank you{name ? `, ${name}` : ""}</h1><p>Your application number is:</p><strong className="af-reference">{reference}</strong><Notice><strong>A person makes every decision.</strong><p>{personOnly ? "You asked for a person-only assessment. AI tools will not be used to check your application." : "The automated check does not approve or reject an application."}</p></Notice>
    <section className="af-card"><h2>What happens next</h2><ol className="af-next-steps"><li><span>1</span><div><strong>Staff check your application</strong><p>A grants officer checks your answers and documents.</p></div></li><li><span>2</span><div><strong>We may contact you</strong><p>You may be asked for more information.</p></div></li><li><span>3</span><div><strong>You will be told by letter</strong><p>Study NT will write to you with the outcome.</p></div></li></ol></section>
    <div className="af-submitted-actions"><FlowButton icon="download" onClick={() => window.print()}>Download a copy</FlowButton><FlowButton variant="secondary" icon="person" onClick={() => { window.location.href = "tel:1800193111"; }}>Ask a question</FlowButton></div><div className="af-contact-card"><Icon name="phone" /><div><strong>Ask a question</strong><p>Call <a href="tel:1800193111">1800 193 111</a> or email the Study NT team.</p></div></div><button className="af-text-button" onClick={onHome}>Return to Study NT grant home</button>
  </div>;
}
