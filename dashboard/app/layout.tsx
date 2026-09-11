import type { Metadata } from "next";
import Link from "next/link";
import { Activity, Wrench } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "MISS — Dashboard",
  description: "Middleware de herramientas LLM sobre Spoter. Runs, herramientas y costos.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" className="dark" suppressHydrationWarning>
      <body className="bg-[#090d16] text-slate-100 antialiased min-h-screen">
        <nav className="border-b border-white/5 bg-black/20 px-6 py-3 md:px-12">
          <div className="flex items-center gap-6 text-sm">
            <Link
              href="/"
              className="flex items-center gap-2 text-slate-300 hover:text-white"
            >
              <Activity className="h-4 w-4" />
              Runs
            </Link>
            <Link
              href="/tools"
              className="flex items-center gap-2 text-slate-300 hover:text-white"
            >
              <Wrench className="h-4 w-4" />
              Herramientas
            </Link>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
