import Link from "next/link";

export default function Home() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Contract Keeper Commissioner</p>
        <h1>Run the league. Not the spreadsheet.</h1>
        <p className="lede">
          Contracts, qualifying offers, tags, traded picks, offseason
          workflows, Trade Lab, and League Health in one commissioner app.
        </p>
        <Link className="primaryButton" href="/leagues/new">
          Create a League
        </Link>
      </section>
    </main>
  );
}
