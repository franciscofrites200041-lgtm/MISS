import { NextResponse, type NextRequest } from "next/server";

// Basic auth para todo el dashboard, reusando las creds que ya usa el server
// para hablarle a MISS. Si están vacías el dashboard queda abierto (fail-open
// coherente con el webhook: se cierra seteando las envs en Portainer).
const USER = process.env.MISS_API_USER || "";
const PASS = process.env.MISS_API_PASS || "";

function unauthorized() {
  return new NextResponse("Auth required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="MISS"' },
  });
}

export function middleware(req: NextRequest) {
  if (!USER || !PASS) return NextResponse.next();

  const header = req.headers.get("authorization") || "";
  if (!header.toLowerCase().startsWith("basic ")) return unauthorized();

  try {
    const decoded = atob(header.slice(6).trim());
    const idx = decoded.indexOf(":");
    if (idx < 0) return unauthorized();
    const user = decoded.slice(0, idx);
    const pass = decoded.slice(idx + 1);
    if (user !== USER || pass !== PASS) return unauthorized();
  } catch {
    return unauthorized();
  }

  return NextResponse.next();
}

export const config = {
  // Todo excepto los estáticos internos de Next. La proxy route /api/tools
  // también queda protegida porque cualquier llamada viene del navegador ya
  // autenticado, y el server-to-server ya usa las creds de MISS_API_USER/PASS.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
