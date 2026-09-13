export async function GET(
  _request: Request,
  context: {
    params: Promise<{
      leagueKey: string;
      seasonYear: string;
    }>;
  },
) {
  const apiBase = process.env.COMMERCIAL_API_URL;

  if (!apiBase) {
    return Response.json(
      { detail: "Commissioner Tools API is not configured." },
      { status: 503 },
    );
  }

  const { leagueKey, seasonYear } = await context.params;

  try {
    const upstream = await fetch(
      `${apiBase.replace(/\/$/, "")}/api/leagues/` +
        `${encodeURIComponent(leagueKey)}/` +
        `${encodeURIComponent(seasonYear)}`,
      {
        method: "GET",
        cache: "no-store",
      },
    );

    const responseBody = await upstream.text();

    return new Response(responseBody, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ??
          "application/json",
      },
    });
  } catch {
    return Response.json(
      { detail: "Commissioner Tools could not load this league." },
      { status: 502 },
    );
  }
}
