"use client";

import { useState, useEffect, useRef } from "react";
import { ThemeToggle } from "./theme-toggle";
import { Settings, LogOut } from 'lucide-react';
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/useAuth";
import { BrandLogo } from "./brand-logo";
import { UsageBadge } from "./usage-badge";
import { OrgSwitcher } from "./org-switcher";
import { shouldShowNavbar } from "@/lib/auth-pages";
import { Button } from "@/components/ui/button";

export function Navbar() {
  const pathname = usePathname();
  const { isAuthenticated, user: currentUser, loading, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Check if navbar should be shown based on current page and auth state
  const showNavbar = shouldShowNavbar(pathname, isAuthenticated);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };

    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open]);

  const handleLogout = async () => {
    try {
      setOpen(false);
      await logout();
      // Force redirect to login page
      window.location.href = "/login";
    } catch (error) {
      console.error("Logout error:", error);
      // Force redirect even if logout fails
      window.location.href = "/login";
    }
  };

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
              {isAuthenticated && <OrgSwitcher />}
              {isAuthenticated && <UsageBadge />}
              {isAuthenticated && currentUser && (
                <div className="relative" ref={dropdownRef}>
                  <Button
                    type="button"
                    onClick={() => setOpen((v) => !v)}
                    variant="ghost"
                    size="icon"
                    aria-expanded={open}
                    aria-haspopup="menu"
                    className={`h-10 w-10 shrink-0 rounded-xl p-0 shadow-md ring-2 ring-white/25 dark:ring-white/10 bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to hover:opacity-95 hover:ring-white/35 focus-visible:ring-saramsa-brand/40 ${open ? "ring-saramsa-brand/50" : ""}`}
                  >
                    <span className="text-sm font-bold tracking-tight text-white drop-shadow-sm">
                      {(currentUser.first_name?.charAt(0) || currentUser.email?.charAt(0) || 'U').toUpperCase()}
                    </span>
                  </Button>
                  {open && (
                    <div
                      role="menu"
                      className="absolute z-50 right-0 mt-2 w-[min(15rem,calc(100vw-2rem))] origin-top-right rounded-lg border border-border/60 bg-popover text-left shadow-lg dark:bg-popover/95 animate-in slide-in-from-top-2 duration-200"
                    >
                      <div className="px-3 py-2.5 border-b border-border/60 bg-secondary/50 dark:bg-secondary/30 rounded-t-lg">
                        <div className="text-xs font-semibold text-popover-foreground line-clamp-1">
                          {[currentUser.first_name, currentUser.last_name].filter(Boolean).join(' ') || currentUser.email || 'User'}
                        </div>
                        <div className="text-[11px] text-muted-foreground line-clamp-1 mt-0.5">
                          {currentUser.email || "No email"}
                        </div>
                      </div>
                      <div className="py-0.5">
                        <Button
                          role="menuitem"
                          onClick={() => {
                            setOpen(false);
                            window.location.href = "/settings";
                          }}
                          variant="ghost"
                          className="flex items-center justify-start gap-2 w-full h-9 px-3 text-sm text-foreground hover:bg-accent/60 rounded-none"
                        >
                          <Settings className="w-3.5 h-3.5 shrink-0" />
                          Settings
                        </Button>
                        <Button
                          role="menuitem"
                          onClick={handleLogout}
                          variant="ghost"
                          className="flex items-center justify-start gap-2 w-full h-9 px-3 text-sm text-destructive hover:bg-accent/60 hover:text-destructive dark:hover:bg-destructive/10 rounded-none"
                        >
                          <LogOut className="w-3.5 h-3.5 shrink-0" />
                          Logout
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <ThemeToggle />

          
            </div>
          </div>
        </div>
      </nav>
    </>
  );
}

