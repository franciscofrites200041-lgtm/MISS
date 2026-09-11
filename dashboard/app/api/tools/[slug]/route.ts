import { NextResponse } from "next/server";
import { updateTool } from "@/lib/api";
import type { ToolPatch } from "@/lib/types";

// Proxy client-side -> API MISS. El basic auth vive en el server para no
// exponer las creds al navegador.
export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  let patch: ToolPatch;
  try {
    patch = (await req.json()) as ToolPatch;
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  try {
    const updated = await updateTool(slug, patch);
    return NextResponse.json(updated);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
