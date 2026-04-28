import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { AuthGate } from "@/components/auth-gate";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 overflow-auto">
          <AuthGate>
            <div className="max-w-7xl mx-auto px-5 py-5">{children}</div>
          </AuthGate>
        </main>
      </div>
    </div>
  );
}
