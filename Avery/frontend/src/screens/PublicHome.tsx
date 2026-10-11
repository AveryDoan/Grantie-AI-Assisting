// Public landing page, from the Figma Make design (ApplicantHome).
import { Button, Icon } from "../ui";
import studyNtLogo from "../assets/study-nt-logo.svg";
import grantieLogo from "../assets/grantie-logo.png";

export default function PublicHome({ onApply, onOfficer }: { onApply: () => void; onOfficer: () => void }) {
  return <div className="public-page">
    <header className="applicant-header">
      <button className="applicant-brand" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} aria-label="Study NT scholarship home">
        <img src={studyNtLogo} alt="Study NT" className="public-logo" />
        <span><strong>Study NT Scholarship</strong><small>Application check</small></span>
      </button>
      <nav className="public-nav" aria-label="Applicant navigation">
        <button onClick={() => document.getElementById("how-ai")?.scrollIntoView({ behavior: "smooth" })}>How it works</button>
        <button onClick={onApply}>Apply</button>
        <Button variant="secondary" onClick={onOfficer}>Officer workspace</Button>
      </nav>
    </header>
    <main>
      <section className="public-hero">
        <div className="hero-glow hero-glow-one" />
        <div className="hero-glow hero-glow-two" />
        <div className="hero-content">
          <p className="hero-kicker"><Icon name="shield" size={17} />A person makes every grant decision</p>
          <h1>Moving to Darwin to study?</h1>
          <p className="hero-lead">Apply for the Study in Australia’s Northern Territory Scholarship. Find missing details before you submit and know what happens next.</p>
          <div className="hero-actions"><Button onClick={onApply}>Start my application <Icon name="arrow" /></Button><Button variant="secondary" icon="user" onClick={() => { window.location.href = "tel:1800193111"; }}>Talk to a person</Button></div>
          <p className="hero-note"><Icon name="check" size={16} />Save and come back later · You can submit anyway</p>
        </div>
        <div className="hero-visual">
          <div className="photo-placeholder hero-photo" role="img" aria-label="Placeholder for an approved photo of Darwin waterfront at sunset">
            <span>Photo placeholder</span><strong>Darwin waterfront or Top End sunset</strong>
          </div>
          <div className="floating-check">
            <div className="mockup-head"><span className="mini-logo">NT</span><div><strong>Application check</strong><small>Step 6 of 7</small></div><span className="mockup-ready">Ready</span></div>
            <div className="mini-progress"><i /></div>
            <div className="mockup-item"><span className="mini-state green"><Icon name="check" /></span><div><strong>Your details are complete</strong><small>We found the information we need.</small></div></div>
            <div className="mockup-item"><span className="mini-state amber"><Icon name="file" /></span><div><strong>Check your enrolment document</strong><small>Make sure your name and course are clear.</small></div></div>
            <div className="mockup-footer"><Icon name="shield" /><span>AI checks. A person decides.</span></div>
          </div>
        </div>
      </section>

      <section className="public-section features-section">
        <div className="section-intro"><p className="eyebrow">A clearer application</p><h2>Helpful checks. Plain answers. Human decisions.</h2><p>The check points you to details worth another look. It never approves or rejects your application.</p></div>
        <div className="feature-grid">
          <article className="feature-card"><span><Icon name="search" size={23} /></span><p className="feature-number">01</p><h3>Find problems before you submit</h3><p>See missing fields and documents while there is still time to fix them.</p><button onClick={onApply}>Start the application <Icon name="arrow" /></button></article>
          <article className="feature-card ochre"><span><Icon name="file" size={23} /></span><p className="feature-number">02</p><h3>Clear reasons, not generic emails</h3><p>If we need more information, we tell you what is missing and what can help.</p></article>
          <article className="feature-card coral"><span><Icon name="user" size={23} /></span><p className="feature-number">03</p><h3>A person always makes the decision</h3><p>A grants officer checks the evidence and is responsible for every outcome.</p></article>
        </div>
      </section>

      <section className="public-section ai-explainer" id="how-ai">
        <div className="photo-placeholder campus-photo" role="img" aria-label="Placeholder for an approved photo of international students on a Northern Territory university campus">
          <span>Photo placeholder</span><strong>International students on campus</strong>
        </div>
        <div className="ai-copy"><p className="eyebrow">How we use AI</p><h2>AI helps you find things. It does not make the call.</h2><div className="plain-steps">
          <div><span>1</span><p><strong>Your personal details are hidden first</strong>Names, contact details, addresses and ID numbers are replaced with placeholders before any AI reads your application.</p></div>
          <div><span>2</span><p><strong>It points to the source</strong>Every suggestion links back to the words in your application.</p></div>
          <div><span>3</span><p><strong>A person checks every finding</strong>An officer confirms or changes each suggestion before deciding.</p></div>
        </div><div className="manual-callout"><Icon name="user" /><div><strong>Prefer not to use the AI check?</strong><p>You can ask for a person-only assessment when you apply.</p></div><Button variant="secondary" onClick={onApply}>Apply with a person-only check</Button></div></div>
      </section>

      <section className="public-section final-cta"><span className="sun-orb" /><p className="eyebrow">Ready when you are</p><h2>Take one more look before you submit.</h2><p>You stay in control. Fix a detail, ask for help, or submit your application anyway.</p><div><Button onClick={onApply}>Start my application <Icon name="arrow" /></Button><Button variant="secondary" icon="user" onClick={() => { window.location.href = "tel:1800193111"; }}>Talk to a person</Button></div></section>
    </main>
    <footer className="public-footer">
      <div className="footer-top">
        <div><img src={studyNtLogo} alt="Study NT" className="public-logo light" /><p>A clear, human-led way to apply for the Study NT scholarship.</p></div>
        <div className="country-placeholder"><strong>Acknowledgement of Country</strong><p>Placeholder — wording to be written and approved by the right people.</p></div>
        <div className="footer-links"><button>Privacy</button><button>Accessibility</button><button onClick={() => { window.location.href = "tel:1800193111"; }}>Contact us</button></div>
      </div>
      <div className="footer-bottom"><img src={grantieLogo} alt="Grantie" className="footer-grantie" /> Test system · A person makes every decision.</div>
    </footer>
  </div>;
}
