import { NextResponse } from "next/server";
import { forward } from "@/lib/proxy";

const ALLOWED = new Set(["approve", "reject", "cancel"]);

export async function POST(req: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await params;
  if (!/^\d+$/.test(id) || !ALLOWED.has(action)) return NextResponse.json({ error: "Bad request" }, { status: 400 });
  return forward(req, `/api/tickets/${id}/${action}`);
}
