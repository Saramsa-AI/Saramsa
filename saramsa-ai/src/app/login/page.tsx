'use client';

import { useState, useEffect, Suspense } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Eye, EyeOff, Mail, Lock, ArrowRight } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import dynamic from 'next/dynamic';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/lib/useAuth';
import { apiRequest } from '@/lib/apiRequest';
import * as authApi from '@/lib/auth';
import { encryptProjectId } from '@/lib/encryption';
import { DataStream, TaskCards, AIProcessing } from '@/components/ui/animations';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

// Lazy-load components to avoid SSR issues
const ThemeToggle = dynamic(
  () => import('@/components/ui/theme-toggle').then(mod => mod.ThemeToggle),
  { ssr: false }
);


const BrandLogo = dynamic(
  () => import('@/components/ui/brand-logo').then(mod => mod.BrandLogo),
  { ssr: false }
);

// Form validation schema
const loginSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
  password: z.string().min(6, 'Password must be at least 6 characters'),
});

type LoginFormData = z.infer<typeof loginSchema>;

function LoginPageInner() {
  const [mounted, setMounted] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const searchParams = useSearchParams();
  const inviteToken = searchParams?.get('invite') || '';

  // Middleware sets `?redirect=<original_path>` when redirecting an
  // unauthenticated user away from a protected route. Honor that here so
  // they land back on the page they wanted instead of the default home.
  //
  // Open-redirect guard: only accept same-origin absolute paths. We refuse
  // protocol-relative URLs ("//evil.com/..."), full URLs ("https://..."),
  // and anything that doesn't start with "/". Returning null falls back to
  // the default routing logic below.
  const safeRedirectTarget = (() => {
    const raw = searchParams?.get('redirect');
    if (!raw) return null;
    if (!raw.startsWith('/')) return null;
    if (raw.startsWith('//')) return null;
    if (raw.includes('://')) return null;
    return raw;
  })();
  const { login } = useAuth();

  // Handle component mount
  useEffect(() => {
    setMounted(true);
    
    // Cleanup function
    return () => {
      // Any cleanup code if needed
    };
  }, []);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  });


  const onSubmit = async (data: LoginFormData) => {
    setIsLoading(true);
    setError(null);
    
    try {
      const result = await login({
        email: data.email,
        password: data.password,
      });
      
      if (result.success) {
        // If we got here via an invite link, accept the invite first so
        // the user lands inside the inviting workspace instead of their
        // default one. Acceptance rotates tokens server-side.
        if (inviteToken) {
          try {
            await authApi.acceptInviteAsLoggedInUser(inviteToken);
            // After invite accept, prefer the original redirect target if
            // the user was sent here from a protected route. Falls back to
            // /projects which is the default invite-acceptance landing.
            router.push(safeRedirectTarget ?? '/projects');
            return;
          } catch (err: any) {
            // Surface the error but don't strand them on the login page —
            // they did successfully log in, just couldn't accept the
            // invite (already used, expired, wrong email, etc.).
            setError(err?.message || 'Logged in, but the invite could not be accepted.');
          }
        }

        // If the user was redirected here from a protected route, send
        // them back there. The project-discovery below is only the
        // fallback path for a "fresh" login from the login page directly.
        if (safeRedirectTarget) {
          router.push(safeRedirectTarget);
          return;
        }

        // After successful login, fetch user's projects and route accordingly
        try {
          const res = await apiRequest('get', '/integrations/projects/list/', undefined, true);
          const projects = res.data?.projects || [];
          if (projects.length > 0) {
            const first = projects[0];
            const projectId = first?.id || first?.project_id;
            if (projectId) {
              router.push(`/projects/${encryptProjectId(projectId)}/dashboard/`);
            } else {
              router.push('/');
            }
          } else {
            // If no projects exist, treat setup as complete when any supported
            // work-management integration is already connected. The config flow
            // should behave like onboarding, not a recurring login checkpoint.
            try {
              const saved = await apiRequest('get', '/integrations/', undefined, true);
              if (saved.data?.success) {
                const accounts = saved.data?.data?.accounts || [];
                const hasConfiguredWorkIntegration = accounts.some(
                  (acc: any) =>
                    ['azure', 'jira', 'asana', 'linear'].includes(acc.provider) &&
                    acc.status === 'active'
                );

                if (hasConfiguredWorkIntegration) {
                  router.push('/projects/');
                } else {
                  router.push('/config/');
                }
              } else {
                router.push('/config/');
              }
            } catch (integrationLookupError) {
              console.error('Failed to inspect integration state after login:', integrationLookupError);
              setError('Logged in, but Saramsa could not verify your integration state. Open the project workspace and retry.');
              router.push('/projects/');
            }
          }
        } catch (projectsLookupError) {
          console.error('Failed to fetch projects after login:', projectsLookupError);
          setError('Logged in, but Saramsa could not load your projects. Open the project workspace and retry.');
          router.push('/projects/');
        }
      } else {
        setError(result.error || 'Login failed. Please check your credentials and try again.');
      }
    } catch (error: unknown) {
      console.error('Login error:', error);
      setError('Login failed. Please check your credentials and try again.');
    } finally {
      setIsLoading(false);
    }
  };

  // Removed unused handleThirdPartyLogin function

  if (!mounted) return null;

  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-background text-foreground transition-colors duration-300 relative">
      {/* Theme toggle button */}
      <div className="fixed top-3 right-3 sm:top-4 sm:right-4 md:top-6 md:right-6 z-50">
        <ThemeToggle />
      </div>

      {/* Left Visual Pane - Hidden on mobile, visible on tablet+ */}
      <div className="hidden md:flex md:w-full lg:w-1/2 xl:w-1/2 2xl:w-1/2 relative bg-gradient-to-br from-background via-secondary/80 to-saramsa-brand/20 dark:from-background dark:via-muted/60 dark:to-accent/40 overflow-hidden min-h-[40vh] lg:min-h-screen">
        <div className="absolute inset-0 bg-gradient-to-br from-saramsa-brand/15 via-transparent to-saramsa-gradient-to/15 dark:from-saramsa-brand/10 dark:to-saramsa-gradient-to/10" />
        
        {/* Data Stream Animation - Hidden on tablet/iPad */}
        {/* <div className="hidden lg:block">
          <DataStream />
        </div> */}
        
        {/* Floating Task Cards - Hidden on tablet/iPad */}
        <div className="hidden lg:block">
          <TaskCards variant="login" />
        </div>
        
        <div className="relative z-10 flex flex-col justify-center px-6 md:px-8 lg:px-12 py-8 lg:py-0">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.5 }}
            className="max-w-full mx-auto lg:mx-0"
          >
            <h1 className="text-2xl md:text-3xl lg:text-4xl xl:text-2xl 2xl:text-4xl font-semibold mb-4 md:mb-6 text-foreground leading-tight">
              Transform Feedback into Action
            </h1>
            <p className="text-sm md:text-base lg:text-lg xl:text-sm 2xl:text-lg text-muted-foreground mb-6 md:mb-8 leading-relaxed">
              Saramsa AI processes customer feedback and automatically identifies actionable insights,
              giving product teams the insights to build better products.
            </p>
            <div className="space-y-3 md:space-y-4">
              {[
                'AI-powered feedback analysis',
                'Automated Jira task creation',
                'Smart priority recommendations',
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
            
            {/* AI Processing Animation - Hidden on tablet/iPad */}
            <div className="hidden lg:block">
              <AIProcessing />
            </div>
          </motion.div>
        </div>
      </div>

      {/* Right Login Form */}
      <div className="w-full md:w-full lg:w-1/2 xl:w-1/2 2xl:w-1/2 flex items-center justify-center p-4 sm:p-6 md:p-8 lg:p-8 xl:p-12 bg-card md:border-l border-border dark:border-border/60 dark:bg-card/70 relative overflow-y-auto min-h-screen md:min-h-[60vh] lg:min-h-screen">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.8 }}
          className="w-full max-w-md mx-auto space-y-6 sm:space-y-8"
        >
          {/* Logo */}
          <div className="text-center">
            <motion.div
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.2 }}
              className="mb-6 sm:mb-8"
            >
              <BrandLogo size="lg" className="justify-center" />
            </motion.div>
          </div>

          {/* Header */}
          <div className="text-center">
            <p className="mt-2 sm:mt-3 text-sm sm:text-base md:text-lg lg:text-base xl:text-xs 2xl:text-base text-muted-foreground leading-relaxed px-2 sm:px-0">
              AI-driven feedback analysis and task automation for Product Managers
            </p>
          </div>

          {/* Error Message */}
          {error && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-destructive/10 border border-destructive/20 rounded-2xl p-4"
            >
              <p className="text-sm text-destructive">{error}</p>
            </motion.div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 sm:space-y-6">
            <div className="space-y-3 sm:space-y-4">
              {/* Email */}
              <div>
                <label htmlFor="email" className="block text-xs sm:text-sm md:text-base lg:text-sm xl:text-xs 2xl:text-sm font-medium text-foreground mb-1 sm:mb-2">
                  Email Address
                </label>
                <div className="relative mt-1 sm:mt-2">
                  <Input
                    {...register('email')}
                    id="email"
                    type="email"
                    placeholder="Enter your email address"
                    className="w-full pl-8 sm:pl-10 pr-3 py-2.5 sm:py-3 text-sm sm:text-sm md:text-sm lg:text-sm xl:text-sm 2xl:text-sm bg-background border border-border rounded-2xl focus:border-saramsa-brand/50 focus:ring-2 focus:ring-saramsa-brand/20 focus:outline-none transition-all duration-300 text-foreground placeholder:text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)] dark:bg-background/80 dark:border-border/60 dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]"
                  />
                  <Mail className="absolute left-2.5 sm:left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 sm:w-4 sm:h-4 text-muted-foreground" />
                </div>
                {errors.email && (
                  <p className="mt-1 text-xs sm:text-sm text-red-500">{errors.email.message}</p>
                )}
              </div>

              {/* Password */}
              <div>
                <label htmlFor="password" className="block text-xs sm:text-sm md:text-base lg:text-sm xl:text-xs 2xl:text-sm font-medium text-foreground mb-1 sm:mb-2">
                  Password
                </label>
                <div className="relative mt-1 sm:mt-2">
                  <Input
                    {...register('password')}
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="Enter your password"
                    className="w-full pl-8 sm:pl-10 pr-8 sm:pr-10 py-2.5 sm:py-3 text-sm sm:text-base md:text-lg lg:text-base xl:text-xs 2xl:text-base bg-background border border-border rounded-2xl focus:border-saramsa-brand/50 focus:ring-2 focus:ring-saramsa-brand/20 focus:outline-none transition-all duration-300 text-foreground placeholder:text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)] dark:bg-background/80 dark:border-border/60 dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]"
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
                  <p className="mt-1 text-xs sm:text-sm text-red-500">{errors.password.message}</p>
                )}
              </div>
            </div>

            {/* Forgot Password */}
            <div className="text-right">
              <Link
                href="/forgot-password"
                className="text-xs sm:text-sm md:text-base lg:text-sm xl:text-xs 2xl:text-sm text-saramsa-brand hover:text-saramsa-brand-hover transition-colors"
              >
                Forgot password?
              </Link>
            </div>

            {/* Login CTA */}
            <Button
              type="submit"
              disabled={isLoading}
              variant="saramsa"
              className="w-full py-2.5 sm:py-3 text-sm sm:text-base md:text-lg lg:text-base xl:text-xs 2xl:text-base group"
            >
              {isLoading ? (
                <div className="w-4 h-4 sm:w-5 sm:h-5 border-2 border-white/30 border-t-white rounded-full animate-spin mx-auto" />
              ) : (
                <>
                  Login
                  <ArrowRight className="ml-2 w-3.5 h-3.5 sm:w-4 sm:h-4 group-hover:translate-x-1 transition-transform inline" />
                </>
              )}
            </Button>

            {/* Register link only shown when an invite token is present.
                Saramsa is invite-only — there is no public sign-up path. */}
            {inviteToken && (
              <div className="text-center">
                <p className="text-xs sm:text-sm md:text-base lg:text-sm xl:text-xs 2xl:text-sm text-muted-foreground">
                  New to Saramsa AI?{' '}
                  <Link
                    href={`/register?invite=${encodeURIComponent(inviteToken)}`}
                    className="text-saramsa-brand hover:text-saramsa-brand-hover font-medium transition-colors"
                  >
                    Accept invitation
                  </Link>
                </p>
              </div>
            )}
          </form>
        </motion.div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  );
}


