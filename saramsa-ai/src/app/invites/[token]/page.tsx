"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Building2, Check, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import * as authApi from "@/lib/auth";
import type { InviteContext } from "@/lib/auth";

export default function AcceptInvitePage() {
  const params = useParams<{ token: string }>();
  const token = params?.token || "";
  const router = useRouter();

  const [invite, setInvite] = useState<InviteContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [authedEmail, setAuthedEmail] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setError("This invite link is invalid.");
      setLoading(false);
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const data = await authApi.lookupInvite(token);
        if (cancelled) return;
        setInvite(data);

        // If the user is logged in, sanity-check the email matches the
        // invite. If not, redirect them to the signup form (we treat
        // anonymous + email-mismatch the same — both need to land on
        // signup with the invite token attached).
        const stored = authApi.getStoredUser();
        const access = authApi.getValidAccessToken();
        if (!access) {
          router.replace(`/register?invite=${encodeURIComponent(token)}`);
          return;
        }
        if (stored?.email && stored.email.toLowerCase() !== data.email.toLowerCase()) {
          setAuthedEmail(stored.email);
          setError(
            `This invite was sent to ${data.email}. You're signed in as ${stored.email}. Sign out and use the invited email, or ask the inviter to resend to ${stored.email}.`,
          );
          return;
        }
        setAuthedEmail(stored?.email || null);
      } catch (err: any) {
        if (cancelled) return;
        setError(err?.message || "This invite link is invalid.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, router]);

  const handleAccept = async () => {
    if (!token) return;
    try {
      setAccepting(true);
      setError(null);
      await authApi.acceptInviteAsLoggedInUser(token);
      // Hard reload so the navbar / org switcher pick up the new
      // active org from the freshly minted JWT.
      window.location.href = "/projects";
    } catch (err: any) {
      setError(err?.message || "Failed to accept invitation.");
      setAccepting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background text-foreground">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading invitation…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background text-foreground p-4">
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 sm:p-8 shadow-sm space-y-5">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to text-white shadow-lg">
            <Building2 className="h-6 w-6" />
          </span>
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Workspace invitation</p>
            <h1 className="text-lg font-semibold text-foreground truncate">
              {invite?.organization.name || "Saramsa workspace"}
            </h1>
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
            <X className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!error && invite && (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>
              You've been invited to join{" "}
              <span className="font-medium text-foreground">{invite.organization.name || "this workspace"}</span>{" "}
              as <span className="font-medium text-foreground">{invite.role}</span>.
            </p>
            {authedEmail && (
              <p className="text-xs">
                Signed in as <span className="font-medium text-foreground">{authedEmail}</span>.
              </p>
            )}
            <p className="text-xs">
              This invite expires {new Date(invite.expires_at).toLocaleString()}.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-2 pt-1">
          {!error && invite && (
            <Button
              variant="saramsa"
              onClick={handleAccept}
              disabled={accepting}
              className="w-full h-10 flex items-center justify-center gap-2"
            >
              {accepting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              Accept invitation
            </Button>
          )}
          {error && (
            <Button
              variant="outline"
              onClick={() => router.push("/projects")}
              className="w-full h-10"
            >
              Back to your workspace
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
