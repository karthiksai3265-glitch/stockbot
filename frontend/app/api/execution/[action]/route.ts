import { NextResponse } from "next/server";
import { forward } from "@/lib/proxy";

const ALLOWED = new Set(["halt", "resume"]);

export async function POST(req: Request, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  if (!ALLOWED.has(action)) return NextResponse.json({ error: "Bad request" }, { status: 400 });
  return forward(req, `/api/execution/${action}`);
}
