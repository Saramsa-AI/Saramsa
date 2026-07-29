"use client";

import { ThemeToggle } from "./theme-toggle";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/useAuth";
import { BrandLogo } from "./brand-logo";
import { UsageBadge } from "./usage-badge";
import { WorkspaceMenu } from "./workspace-menu";
import { shouldShowNavbar } from "@/lib/auth-pages";

export function Navbar() {
  const pathname = usePathname();
  const { isAuthenticated, loading } = useAuth();

  // Check if navbar should be shown based on current page and auth state
  const showNavbar = shouldShowNavbar(pathname, isAuthenticated);

  // Don't render until auth state is determined
  if (loading) {
    return null;
  }

  // Hide navbar on auth pages or when not authenticated on home page
  if (!showNavbar) {
    return null;
  }

  return (
    <>
    <nav className="z-100 w-full sticky top-0 bg-card dark:bg-background border-b border-border dark:border-border/60 shadow-md dark:shadow-sm">
        <div className="px-4 sm:px-6 lg:px-10">
          <div className="flex justify-between items-center h-16 lg:h-18">
            {/* Logo */}
            <div className="flex-shrink-0">
              <Link href="/projects">
                <BrandLogo size="md" />
              </Link>
            </div>

            {/* Right side - Usage, Theme Toggle and Profile */}
            <div className="flex items-center gap-3 sm:gap-4">
              {isAuthenticated && <UsageBadge />}
              {/* Workspace name + account actions are one menu now (see
                  WorkspaceMenu). The old OrgSwitcher and first-letter avatar
                  split four related actions across two adjacent triggers. */}
              {isAuthenticated && <WorkspaceMenu />}
              <ThemeToggle />

          
            </div>
          </div>
        </div>
      </nav>
    </>
  );
}

