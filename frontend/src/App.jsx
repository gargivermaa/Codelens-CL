import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { BarChart3, FlaskConical, FolderGit2, GitPullRequest, LayoutDashboard, Menu, ScanSearch, X } from "lucide-react";
import Overview from "./pages/Overview.jsx";
import Repositories from "./pages/Repositories.jsx";
import RepositoryDetail from "./pages/RepositoryDetail.jsx";
import PullRequests from "./pages/PullRequests.jsx";
import ReviewDetail from "./pages/ReviewDetail.jsx";
import PullDetail from "./pages/PullDetail.jsx";
import Analytics from "./pages/Analytics.jsx";
import Evaluation from "./pages/Evaluation.jsx";
import { Empty } from "./components/ui.jsx";

const NAV = [
  { to: "/", label: "Overview", Icon: LayoutDashboard, end: true },
  { to: "/repositories", label: "Repositories", Icon: FolderGit2 },
  { to: "/pulls", label: "Pull requests", Icon: GitPullRequest },
  { to: "/analytics", label: "Analytics", Icon: BarChart3 },
  { to: "/evaluation", label: "Evaluation", Icon: FlaskConical },
];

function Sidebar({ onNavigate }) {
  return (
    <nav className="flex h-full flex-col gap-1 p-4" aria-label="Main">
      <div className="mb-6 flex items-center gap-2.5 px-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15 text-accent">
          <ScanSearch className="h-5 w-5" aria-hidden />
        </span>
        <div>
          <div className="text-sm font-semibold text-ink">CodeLens CI</div>
          <div className="text-[11px] text-muted">Repository-aware review</div>
        </div>
      </div>
      {NAV.map(({ to, label, Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate}
          className={({ isActive }) => `focus-ring flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
            isActive ? "bg-accent/12 text-ink" : "text-muted hover:bg-raised hover:text-ink"}`}>
          {({ isActive }) => (<>
            <Icon className={`h-4 w-4 ${isActive ? "text-accent" : ""}`} aria-hidden />{label}
          </>)}
        </NavLink>
      ))}
      <div className="mt-auto px-3 text-[11px] leading-relaxed text-faint">
        Reviews run on GitHub pull requests via the CodeLens GitHub App.
      </div>
    </nav>
  );
}

export default function App() {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <aside className="hidden border-r border-line bg-surface/60 lg:block">
        <div className="sticky top-0 h-screen"><Sidebar /></div>
      </aside>
      <div className="flex items-center justify-between border-b border-line bg-surface/60 px-4 py-3 lg:hidden">
        <span className="flex items-center gap-2 text-sm font-semibold"><ScanSearch className="h-5 w-5 text-accent" aria-hidden />CodeLens CI</span>
        <button className="focus-ring rounded-md p-1.5 text-muted" onClick={() => setOpen(!open)} aria-label="Toggle navigation">
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </div>
      {open && <div className="border-b border-line bg-surface lg:hidden"><Sidebar onNavigate={() => setOpen(false)} /></div>}
      <main className="min-w-0 overflow-x-clip px-4 py-6 sm:px-8 sm:py-8">
        <div className="mx-auto max-w-7xl">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/repositories" element={<Repositories />} />
            <Route path="/repositories/:githubId" element={<RepositoryDetail />} />
            <Route path="/pulls" element={<PullRequests />} />
            <Route path="/pulls/:prId" element={<PullDetail />} />
            <Route path="/review/:reviewId" element={<ReviewDetail />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="*" element={<Empty title="Page not found">Use the navigation on the left.</Empty>} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
