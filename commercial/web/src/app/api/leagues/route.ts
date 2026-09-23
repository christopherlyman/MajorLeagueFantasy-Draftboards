const AUTH_COOKIE_NAME = "mlf_auth";

function getForwardedAuthCookie(
  request: Request,
): string | null {
  const cookieHeader = request.headers.get("cookie");

  if (!cookieHeader) {
    return null;
  }

  for (const part of cookieHeader.split(";")) {
    const cookie = part.trim();
    const separator = cookie.indexOf("=");

    if (separator <= 0) {
      continue;
    }

    const name = cookie.slice(0, separator).trim();

    if (name !== AUTH_COOKIE_NAME) {
      continue;
    }

    const value = cookie.slice(separator + 1).trim();

    if (!value) {
      return null;
    }

    return `${AUTH_COOKIE_NAME}=${value}`;
  }

  return null;
}


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
    const authCookie = getForwardedAuthCookie(request);

    const upstream = await fetch(
      `${apiBase.replace(/\/$/, "")}/api/leagues`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authCookie
            ? { Cookie: authCookie }
            : {}),
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
