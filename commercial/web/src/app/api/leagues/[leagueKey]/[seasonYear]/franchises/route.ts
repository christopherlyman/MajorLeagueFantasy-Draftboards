type RouteContext = {
  params: Promise<{
    leagueKey: string;
    seasonYear: string;
  }>;
};

function apiUrl(
  apiBase: string,
  leagueKey: string,
  seasonYear: string,
) {
  return (
    `${apiBase.replace(/\/$/, "")}/api/leagues/` +
    `${encodeURIComponent(leagueKey)}/` +
    `${encodeURIComponent(seasonYear)}/franchises`
  );
}

export async function GET(
  _request: Request,
  context: RouteContext,
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
      apiUrl(apiBase, leagueKey, seasonYear),
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
      {
        detail:
          "Commissioner Tools could not load league franchises.",
      },
      { status: 502 },
    );
  }
}

export async function POST(
  request: Request,
  context: RouteContext,
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
    const requestBody = await request.text();

    const upstream = await fetch(
      apiUrl(apiBase, leagueKey, seasonYear),
      {
        method: "POST",
        headers: {
          "Content-Type":
            request.headers.get("content-type") ??
            "application/json",
        },
        body: requestBody,
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
      {
        detail:
          "Commissioner Tools could not save league franchises.",
      },
      { status: 502 },
    );
  }
}
