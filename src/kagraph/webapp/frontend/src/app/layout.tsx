import type { Metadata } from "next";
import { Fira_Sans, Fira_Code } from "next/font/google";
import "./globals.css";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { LayoutDashboard, Activity, BookOpen, Layers, Terminal } from "lucide-react";

const firaSans = Fira_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-fira-sans",
});

const firaCode = Fira_Code({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-fira-code",
});

export const metadata: Metadata = {
  title: "KaTrace | Observability",
  description: "Graph-based observability for KaGraph benchmark runs",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={cn("dark", firaSans.variable, firaCode.variable)}>
      <body className="flex h-screen w-full bg-background text-foreground overflow-hidden font-sans">
        {/* Sidebar */}
        <div className="flex flex-col w-64 border-r border-border bg-[#0F172A] text-slate-300">
          {/* Logo */}
          <div className="flex items-center h-16 px-6 border-b border-border font-bold text-xl tracking-tight text-white">
            <Layers className="w-5 h-5 text-[#22C55E] mr-2" />
            <span>Ka<span className="text-[#22C55E]">Graph</span></span>
          </div>

          {/* Nav Items */}
          <div className="flex flex-col flex-1 p-4 gap-2 overflow-y-auto">
            <div className="text-xs font-semibold text-slate-500 uppercase px-3 mt-4 mb-2 tracking-wider">
              Orchestration
            </div>
            <Link
              href="/"
              className="flex items-center px-3 py-2.5 rounded-lg text-sm font-medium hover:bg-[#1E293B] hover:text-white transition-all duration-200"
            >
              <LayoutDashboard className="w-4 h-4 mr-3" />
              Dashboard
            </Link>
            <Link
              href="/traces"
              className="flex items-center px-3 py-2.5 rounded-lg text-sm font-medium hover:bg-[#1E293B] hover:text-white transition-all duration-200"
            >
              <Activity className="w-4 h-4 mr-3" />
              Traces
            </Link>
            <Link
              href="/guide"
              className="flex items-center px-3 py-2.5 rounded-lg text-sm font-medium hover:bg-[#1E293B] hover:text-white transition-all duration-200"
            >
              <BookOpen className="w-4 h-4 mr-3" />
              Guide
            </Link>
            <Link
              href="/studio"
              className="flex items-center px-3 py-2.5 rounded-lg text-sm font-medium hover:bg-[#1E293B] hover:text-white transition-all duration-200"
            >
              <Terminal className="w-4 h-4 mr-3" />
              Studio Logs
            </Link>
          </div>
        </div>
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-background">
          {children}
        </main>
      </body>
    </html>
  );
}
