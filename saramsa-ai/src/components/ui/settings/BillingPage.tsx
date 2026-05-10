"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, CircleDashed, ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  createStripeBillingPortalSession,
  createStripeCheckoutSession,
  getStripeSubscriptionStatus,
  type SubscriptionStatus,
} from "@/lib/billingService";

export function BillingPage() {
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<"checkout" | "portal" | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const s = await getStripeSubscriptionStatus();
        setStatus(s);
      } catch (e: any) {
        setError(e?.message || "Failed to load billing status.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleStartSubscription = async () => {
    try {
      setActionLoading("checkout");
      const data = await createStripeCheckoutSession();
      if (data?.checkout_url) {
        window.location.href = data.checkout_url;
      }
    } catch (e: any) {
      setError(e?.message || "Failed to start checkout.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleOpenBillingPortal = async () => {
    try {
      setActionLoading("portal");
      const data = await createStripeBillingPortalSession();
      if (data?.portal_url) {
        window.location.href = data.portal_url;
      }
    } catch (e: any) {
      setError(e?.message || "Failed to open billing portal.");
    } finally {
      setActionLoading(null);
    }
  };

  const isSubscribed = !!status?.is_subscribed;
  const statusLabel = loading
    ? "Loading…"
    : isSubscribed
      ? `Active · ${status?.subscription_status ?? "subscribed"}`
      : `Not active · ${status?.subscription_status || "inactive"}`;

  return (
    <div className="space-y-8">
      <header>
        <h2 className="text-lg font-semibold text-foreground">Billing</h2>
        <p className="text-sm text-muted-foreground mt-2">Subscription and billing preferences.</p>
      </header>

      <section className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-6 py-4">
          <h3 className="text-sm font-medium text-foreground">Subscription</h3>
        </div>
        <div className="px-6 py-5 space-y-5">
          <div className="flex items-start gap-3">
            <span
              className={`inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full ${
                isSubscribed ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-muted text-muted-foreground"
              }`}
            >
              {isSubscribed ? <CheckCircle2 className="h-4 w-4" /> : <CircleDashed className="h-4 w-4" />}
            </span>
            <div>
              <p className="text-sm font-medium text-foreground">{statusLabel}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {isSubscribed
                  ? "Your workspace has an active subscription."
                  : "Activate a subscription to unlock paid features."}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            {!isSubscribed && (
              <Button
                variant="saramsa"
                size="sm"
                onClick={handleStartSubscription}
                disabled={loading || actionLoading !== null}
                className="gap-2"
              >
                {actionLoading === "checkout" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : null}
                Start subscription
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={handleOpenBillingPortal}
              disabled={loading || actionLoading !== null}
              className="gap-2"
            >
              {actionLoading === "portal" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ExternalLink className="h-4 w-4" />
              )}
              Manage in Stripe
            </Button>
          </div>

          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}
        </div>
      </section>
    </div>
  );
}
