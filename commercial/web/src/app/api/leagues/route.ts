export async function POST(request: Request) {
  const apiBase = process.env.COMMERCIAL_API_URL;

  if (!apiBase) {
    return Response.json(
      { detail: "Commissioner Tools API is not configured." },
      { status: 503 },
    );
  }

  try {
    const body = await request.text();

    const upstream = await fetch(
      `${apiBase.replace(/\/$/, "")}/api/leagues`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body,
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
      { detail: "Commissioner Tools could not reach the league service." },
      { status: 502 },
    );
  }
}
