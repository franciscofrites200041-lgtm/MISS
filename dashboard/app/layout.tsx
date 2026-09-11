import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MISS — Dashboard",
  description: "Middleware de transcripción de audios de Spoter. Runs, métricas y costos.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" className="dark" suppressHydrationWarning>
      <body className="bg-[#090d16] text-slate-100 antialiased min-h-screen">
        {children}
      </body>
    </html>
  );
}
