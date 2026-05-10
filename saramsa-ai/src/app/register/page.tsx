'use client';

import { useState, useEffect, Suspense } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Eye, EyeOff, Mail, Lock, ArrowRight, Building2, Shield } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import dynamic from 'next/dynamic';
import { AINodes, AIProcessing } from '@/components/ui/animations';

const BrandLogo = dynamic(
  () => import('@/components/ui/brand-logo').then(mod => mod.BrandLogo),
  { ssr: false }
);

const ThemeToggle = dynamic(
  () => import('@/components/ui/theme-toggle').then(mod => mod.ThemeToggle),
  { ssr: false }
);

import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/lib/useAuth';
import * as authApi from '@/lib/auth';
import type { InviteContext } from '@/lib/auth';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

const registerSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
  password: z.string().min(6, 'Password must be at least 6 characters'),
  confirmPassword: z.string().min(1, 'Please confirm your password'),
}).refine((data) => data.password === data.confirmPassword, {
  message: "Passwords don't match",
  path: ["confirmPassword"],
});

type RegisterFormData = z.input<typeof registerSchema>;

function RegisterPageInner() {
  const searchParams = useSearchParams();
  const inviteToken = searchParams?.get('invite') || '';

  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<InviteContext | null>(null);
  const [inviteLoading, setInviteLoading] = useState<boolean>(!!inviteToken);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const router = useRouter();
  const { register: registerUser } = useAuth();

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<RegisterFormData>({
    resolver: zodResolver(registerSchema),
  });

  // Look up the invite once on mount so we can lock the email field. If
  // the lookup fails (expired, revoked, wrong token) we surface a clean
  // error and prevent submission.
  useEffect(() => {
    if (!inviteToken) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await authApi.lookupInvite(inviteToken);
        if (cancelled) return;
        setInvite(data);
        setValue('email', data.email);
        setInviteError(null);
      } catch (err: any) {
        if (cancelled) return;
        setInviteError(err?.message || 'This invite link is invalid.');
      } finally {
        if (!cancelled) setInviteLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [inviteToken, setValue]);

  const onSubmit = async (data: RegisterFormData) => {
    setIsLoading(true);
    setError(null);

    try {
      const result = await registerUser({
        email: data.email,
        password: data.password,
        confirmPassword: data.confirmPassword,
        invite_token: inviteToken,
      });

      if (result.success) {
        router.push('/projects');
      } else {
        setError(result.error || 'Registration failed. Please try again.');
      }
    } catch (err: any) {
      console.error('Register error:', err);
      setError(err?.message || 'Registration failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-background text-foreground transition-colors duration-300 relative">
      <div className="fixed top-3 right-3 sm:top-4 sm:right-4 md:top-6 md:right-6 z-50">
        <ThemeToggle />
      </div>

      {/* Left visual pane */}
      <div className="hidden md:flex md:w-full lg:w-1/2 relative bg-gradient-to-br from-background via-secondary/80 to-saramsa-brand/20 dark:from-background dark:via-muted/60 dark:to-accent/40 overflow-hidden min-h-[40vh] lg:min-h-screen">
        <div className="absolute inset-0 bg-gradient-to-br from-saramsa-brand/15 via-transparent to-saramsa-gradient-to/15 dark:from-saramsa-brand/10 dark:to-saramsa-gradient-to/10" />
        <div className="hidden lg:block">
          <AINodes />
        </div>
        <div className="relative z-10 flex flex-col justify-center px-6 md:px-8 lg:px-12 py-8 lg:py-0">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.5 }}
            className="max-w-md mx-auto lg:mx-0"
          >
            <h1 className="text-2xl md:text-3xl lg:text-4xl xl:text-2xl 2xl:text-4xl font-semibold mb-4 md:mb-6 text-foreground leading-tight">
              {invite ? `Joining ${invite.organization.name || 'a workspace'}` : 'Saramsa AI'}
            </h1>
            <p className="text-sm md:text-base lg:text-lg xl:text-sm 2xl:text-lg text-muted-foreground mb-6 md:mb-8 leading-relaxed">
              {invite
                ? `You were invited as ${invite.role}. Create your account to accept.`
                : 'Saramsa is invite-only. Ask your workspace admin for an invitation link.'}
            </p>
            <div className="space-y-3 md:space-y-4">
              {[
                'AI-powered feedback analysis',
                'Automated task creation',
                'Smart priority recommendations',
                'Real-time insights dashboard',
              ].map((feature, index) => (
                <motion.div
                  key={index}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.8, delay: 1 + index * 0.2 }}
                  className="flex items-center gap-3"
                >
                  <div className="w-2 h-2 bg-saramsa-brand rounded-full animate-pulse-glow shadow-[0_0_16px_rgba(139,95,191,0.45)] flex-shrink-0" />
                  <span className="text-sm md:text-base xl:text-xs text-muted-foreground">{feature}</span>
                </motion.div>
              ))}
            </div>
            {invite && (
              <div className="hidden lg:block">
                <AIProcessing
                  text="Adding you to workspace"
                  className="mt-6 flex items-center gap-2 text-saramsa-brand"
                />
              </div>
            )}
          </motion.div>
        </div>
      </div>

      {/* Right form */}
      <div className="w-full lg:w-1/2 flex items-center justify-center p-2 sm:p-3 md:p-5 bg-card md:border-l border-border dark:border-border/60 dark:bg-card/70 relative min-h-screen">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.8 }}
          className="w-full max-w-[420px] mx-auto space-y-2.5 sm:space-y-3"
        >
          <div className="text-center">
            <motion.div
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.2 }}
              className="mb-1.5 sm:mb-2"
            >
              <BrandLogo size="md" className="justify-center" />
            </motion.div>
          </div>

          {!inviteToken ? (
            <div className="space-y-4 text-center">
              <div className="mx-auto inline-flex items-center justify-center w-12 h-12 rounded-full bg-saramsa-brand/10 text-saramsa-brand">
                <Shield className="w-6 h-6" />
              </div>
              <h2 className="text-lg sm:text-xl md:text-2xl font-semibold text-foreground leading-tight">
                Invite-only workspace
              </h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Saramsa accounts are created by workspace admins. Ask your admin to send you an invitation link, then open the link in this browser to finish setting up your account.
              </p>
              <Link
                href="/login"
                className="inline-block text-saramsa-brand hover:text-saramsa-brand-hover font-medium text-sm transition-colors"
              >
                Back to sign in
              </Link>
            </div>
          ) : (
            <>
              <div className="text-center">
                <h2 className="text-lg sm:text-xl md:text-2xl font-semibold text-foreground leading-tight">
                  {inviteError ? 'Invitation problem' : 'Accept invitation'}
                </h2>
                {invite && invite.organization.name && (
                  <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Building2 className="h-3.5 w-3.5" />
                    Joining <span className="font-medium text-foreground">{invite.organization.name}</span>
                  </p>
                )}
              </div>

              {inviteError && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-destructive/10 border border-destructive/20 rounded-2xl p-4 space-y-3"
                >
                  <p className="text-sm text-destructive">{inviteError}</p>
                  <p className="text-xs text-muted-foreground">
                    Ask your workspace admin to send a fresh invitation link, then open it in this browser.
                  </p>
                  <Link
                    href="/login"
                    className="inline-block text-saramsa-brand hover:text-saramsa-brand-hover font-medium text-sm transition-colors"
                  >
                    Back to sign in
                  </Link>
                </motion.div>
              )}

              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-destructive/10 border border-destructive/20 rounded-2xl p-4"
                >
                  <p className="text-sm text-destructive">{error}</p>
                </motion.div>
              )}

              {!inviteError && (
                <form onSubmit={handleSubmit(onSubmit)} className="space-y-2 sm:space-y-2.5">
                  <div className="space-y-1.5 sm:space-y-2">
                    <div>
                      <label htmlFor="email" className="block text-xs sm:text-sm font-medium text-foreground mb-1 sm:mb-2">
                        Email address
                      </label>
                      <div className="relative">
                        <Input
                          {...register('email')}
                          id="email"
                          type="email"
                          readOnly
                          placeholder="Enter your email"
                          className="w-full pl-8 sm:pl-10 pr-3 py-1.5 sm:py-2 text-sm bg-background border border-border rounded-2xl focus:border-saramsa-brand/50 focus:ring-2 focus:ring-saramsa-brand/20 focus:outline-none transition-all duration-300 text-foreground placeholder:text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)] dark:bg-background/80 dark:border-border/60 dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.4)] disabled:opacity-70"
                        />
                        <Mail className="absolute left-2.5 sm:left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 sm:w-4 sm:h-4 text-muted-foreground" />
                      </div>
                      {errors.email && (
                        <p className="mt-1 text-xs sm:text-sm text-destructive">{errors.email.message}</p>
                      )}
                    </div>

                    <div>
                      <label htmlFor="password" className="block text-xs sm:text-sm font-medium text-foreground mb-1 sm:mb-2">
                        Password
                      </label>
                      <div className="relative">
                        <Input
                          {...register('password')}
                          id="password"
                          type={showPassword ? 'text' : 'password'}
                          placeholder="Create a password"
                          className="w-full pl-8 sm:pl-10 pr-8 sm:pr-10 py-1.5 sm:py-2 text-sm bg-background border border-border rounded-2xl focus:border-saramsa-brand/50 focus:ring-2 focus:ring-saramsa-brand/20 focus:outline-none transition-all duration-300 text-foreground placeholder:text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)] dark:bg-background/80 dark:border-border/60 dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]"
                        />
                        <Lock className="absolute left-2.5 sm:left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 sm:w-4 sm:h-4 text-muted-foreground" />
                        <Button
                          type="button"
                          onClick={() => setShowPassword(!showPassword)}
                          variant="ghost"
                          size="icon"
                          className="absolute right-2.5 sm:right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-saramsa-brand"
                        >
                          {showPassword ? <EyeOff className="w-3.5 h-3.5 sm:w-4 sm:h-4" /> : <Eye className="w-3.5 h-3.5 sm:w-4 sm:h-4" />}
                        </Button>
                      </div>
                      {errors.password && (
                        <p className="mt-1 text-xs sm:text-sm text-destructive">{errors.password.message}</p>
                      )}
                    </div>

                    <div>
                      <label htmlFor="confirmPassword" className="block text-xs sm:text-sm font-medium text-foreground mb-1 sm:mb-2">
                        Confirm Password
                      </label>
                      <div className="relative">
                        <Input
                          {...register('confirmPassword')}
                          id="confirmPassword"
                          type={showConfirmPassword ? 'text' : 'password'}
                          placeholder="Confirm your password"
                          className="w-full pl-8 sm:pl-10 pr-8 sm:pr-10 py-1.5 sm:py-2 text-sm bg-background border border-border rounded-2xl focus:border-saramsa-brand/50 focus:ring-2 focus:ring-saramsa-brand/20 focus:outline-none transition-all duration-300 text-foreground placeholder:text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)] dark:bg-background/80 dark:border-border/60 dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]"
                        />
                        <Lock className="absolute left-2.5 sm:left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 sm:w-4 sm:h-4 text-muted-foreground" />
                        <Button
                          type="button"
                          onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                          variant="ghost"
                          size="icon"
                          className="absolute right-2.5 sm:right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-saramsa-brand"
                        >
                          {showConfirmPassword ? <EyeOff className="w-3.5 h-3.5 sm:w-4 sm:h-4" /> : <Eye className="w-3.5 h-3.5 sm:w-4 sm:h-4" />}
                        </Button>
                      </div>
                      {errors.confirmPassword && (
                        <p className="mt-1 text-xs sm:text-sm text-destructive">{errors.confirmPassword.message}</p>
                      )}
                    </div>
                  </div>

                  <Button
                    type="submit"
                    disabled={isLoading || inviteLoading}
                    variant="saramsa"
                    className="w-full py-1.5 sm:py-2 text-sm group"
                  >
                    {isLoading || inviteLoading ? (
                      <div className="w-4 h-4 sm:w-5 sm:h-5 border-2 border-white/30 border-t-white rounded-full animate-spin mx-auto" />
                    ) : (
                      <>
                        Accept &amp; create account
                        <ArrowRight className="ml-2 w-3.5 h-3.5 sm:w-4 sm:h-4 group-hover:translate-x-1 transition-transform inline" />
                      </>
                    )}
                  </Button>

                  <div className="text-center">
                    <p className="text-xs sm:text-sm text-muted-foreground">
                      Already have an account?{' '}
                      <Link
                        href={`/login?invite=${encodeURIComponent(inviteToken)}`}
                        className="text-saramsa-brand hover:text-saramsa-brand-hover font-medium transition-colors"
                      >
                        Sign in
                      </Link>
                    </p>
                  </div>
                </form>
              )}
            </>
          )}
        </motion.div>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={null}>
      <RegisterPageInner />
    </Suspense>
  );
}
