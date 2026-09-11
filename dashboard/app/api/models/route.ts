import { NextResponse } from "next/server";
import { fetchModels } from "@/lib/api";

const ALLOWED_KINDS = new Set(["audio", "image", "document"]);

export async function GET(req: Request) {
  const kind = new URL(req.url).searchParams.get("kind") || "";
  if (!ALLOWED_KINDS.has(kind)) {
    return NextResponse.json({ error: "invalid kind" }, { status: 400 });
  }
  try {
    const data = await fetchModels(kind);
    return NextResponse.json(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}