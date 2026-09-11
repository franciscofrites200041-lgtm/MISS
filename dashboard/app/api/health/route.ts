import { NextResponse } from "next/server";

// Endpoint público (excluido del middleware) para el healthcheck del container.
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ ok: true });
}
