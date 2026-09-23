import {
  NextRequest,
  NextResponse,
} from "next/server";


export const dynamic = "force-dynamic";


const API_BASE_URL =
  process.env.MLF_API_INTERNAL_URL
  ?? "http://mlf_api:8000";


export async function GET(
  request: NextRequest,
) {
  try {
    const headers =
      new Headers({
        accept: "application/json",
      });

    const cookie =
      request.headers.get("cookie");

    if (cookie) {
      headers.set(
        "cookie",
        cookie,
      );
    }

    const response =
      await fetch(
        `${API_BASE_URL}/auth/me`,
        {
          method: "GET",
          headers,
          cache: "no-store",
        },
      );

    const body =
      await response.text();

    const outgoingHeaders =
      new Headers({
        "content-type":
          response.headers.get(
            "content-type",
          )
          ?? "application/json",
        "cache-control":
          "no-store",
      });

    const setCookie =
      response.headers.get(
        "set-cookie",
      );

    if (setCookie) {
      outgoingHeaders.set(
        "set-cookie",
        setCookie,
      );
    }

    return new NextResponse(
      body,
      {
        status: response.status,
        headers: outgoingHeaders,
      },
    );
  } catch {
    return NextResponse.json(
      {
        detail: {
          code:
            "service_unavailable",
        },
      },
      {
        status: 503,
        headers: {
          "cache-control":
            "no-store",
        },
      },
    );
  }
}
