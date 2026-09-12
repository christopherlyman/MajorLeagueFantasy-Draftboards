import Link from "next/link";

const platforms = ["Yahoo", "ESPN", "Sleeper", "Fantrax", "Fleaflicker"];

const features = [
  {
    title: "Keeper & Dynasty",
    text: "Track keeper decisions, persistent rosters, future picks, and season-to-season league state.",
  },
  {
    title: "Contract Keeper",
    text: "Manage configurable contracts, expirations, restricted rights, tags, and offseason decisions.",
  },
  {
    title: "Commissioner DraftBoard",
    text: "Run offline drafts with keepers, traded picks, draft history, corrections, and auction support.",
  },
  {
    title: "Trade Analyzer",
    text: "Evaluate trades with explainable player value, team fit, draft assets, and competitive context.",
  },
  {
    title: "League Health",
    text: "See concentration, asset movement, competitive balance, and long-term league trends.",
  },
  {
    title: "One Commissioner Workspace",
    text: "Keep the host your league already uses while moving commissioner work out of spreadsheets.",
  },
];

export default function Home() {
  return (
    <main className="siteShell">
      <nav className="topNav">
        <Link className="brand" href="/">
          <span className="brandMark">C</span>
          <span>
            <strong>Commissioner Tools</strong>
            <small>Fantasy league operations</small>
          </span>
        </Link>

        <div className="navActions">
          <a href="#capabilities">Capabilities</a>
          <Link className="navButton" href="/leagues/new">Create League</Link>
        </div>
      </nav>

      <section className="heroV2">
        <div className="heroCopy">
          <p className="eyebrow">Built for commissioners</p>
          <h1>Run the league.<br /><span>Lose the spreadsheet.</span></h1>
          <p className="heroLead">
            Manage redraft, keeper, dynasty, and contract leagues without replacing
            the fantasy platform your league already uses.
          </p>

          <div className="heroActions">
            <Link className="primaryCta" href="/leagues/new">Create your league</Link>
            <a className="secondaryCta" href="#capabilities">Explore capabilities</a>
          </div>

          <div className="platformStrip">
            <span>Works alongside</span>
            <div className="platforms">
              {platforms.map((platform) => (
                <span key={platform}>{platform}</span>
              ))}
            </div>
          </div>
        </div>

        <aside className="heroPanel">
          <p className="panelLabel">COMMISSIONER WORKSPACE</p>
          <h2>One place to run the parts your host leaves behind.</h2>
          <div className="workflow">
            <span className="done">League setup</span>
            <span>Keeper & contract decisions</span>
            <span>Draft preparation</span>
            <span>Commissioner DraftBoard</span>
            <span>Season rollover</span>
          </div>
          <div className="panelStatus">
            <span className="statusDot" />
            League state stays connected from season to season
          </div>
        </aside>
      </section>

      <section className="promise">
        <p>From simple redraft leagues to custom contract systems</p>
        <h2>Your league can be unique without your administration being manual.</h2>
      </section>

      <section className="capabilities" id="capabilities">
        <div className="sectionHeading">
          <p className="eyebrow">Commissioner infrastructure</p>
          <h2>The work behind the league, handled.</h2>
        </div>

        <div className="featureGrid">
          {features.map((feature, index) => (
            <article className="featureCard" key={feature.title}>
              <span className="featureNumber">{String(index + 1).padStart(2, "0")}</span>
              <h3>{feature.title}</h3>
              <p>{feature.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="bottomCta">
        <div>
          <p className="eyebrow">Start with your league</p>
          <h2>Configure the rules. We will handle the structure.</h2>
        </div>
        <Link className="primaryCta light" href="/leagues/new">Create a League</Link>
      </section>
    </main>
  );
}
