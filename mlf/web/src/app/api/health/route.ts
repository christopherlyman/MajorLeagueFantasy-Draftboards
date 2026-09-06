import { NextResponse } from "next/server";

import { loadBoard } from "@/lib/board";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const revalidate = 0;

export async function GET() {
  const draftKey =
    process.env.DRAFTBOARD_DRAFT_KEY ??
    "mlf_2026_preseason";

  const rows = await loadBoard(draftKey);

  const healthy =
    rows.length === 400;

  return NextResponse.json(
    {
      status: healthy ? "ok" : "degraded",
      draftKey,
      boardRows: rows.length,
      writeCapability: false,
    },
    {
      status: healthy ? 200 : 500,
    },
  );
}