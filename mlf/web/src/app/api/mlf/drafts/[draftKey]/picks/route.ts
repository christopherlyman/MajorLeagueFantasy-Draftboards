import {
  NextRequest,
  NextResponse,
} from "next/server";


export const dynamic = "force-dynamic";


const API_BASE_URL =
  process.env.MLF_API_INTERNAL_URL
  ?? "http://mlf_api:8000";


export async function POST(
  request: NextRequest,
  context: {
    params: Promise<{
      draftKey: string;
    }>;
  },
) {
  const {
    draftKey,
  } = await context.params;

  try {
    const headers =
      new Headers({
        accept: "application/json",
      });

    for (
      const headerName
      of [
        "content-type",
        "cookie",
        "origin",
        "referer",
      ]
    ) {
      const value =
        request.headers.get(
          headerName,
        );

      if (value) {
        headers.set(
          headerName,
          value,
        );
      }
    }

    const body =
      await request.text();

    const response =
      await fetch(
        `${API_BASE_URL}/drafts/${encodeURIComponent(draftKey)}/picks`,
        {
          method: "POST",
          headers,
          body,
          cache: "no-store",
        },
      );

    const responseBody =
      await response.text();

    return new NextResponse(
      responseBody,
      {
        status: response.status,
        headers: {
          "content-type":
            response.headers.get(
              "content-type",
            )
            ?? "application/json",
          "cache-control":
            "no-store",
        },
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
