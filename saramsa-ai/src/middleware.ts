import { NextRequest, NextResponse } from 'next/server';

const PUBLIC_PATHS = new Set(['/login/', '/register/', '/signup/', '/reset-password/']);
const PUBLIC_PREFIXES = ['/api/', '/_next/', '/favicon', '/images/', '/logo/', '/invites/'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PATHS.has(pathname) ||
    PUBLIC_PREFIXES.some((p) => pathname.startsWith(p)) ||
    pathname === '/'
  ) {
    return NextResponse.next();
  }

  const token = request.cookies.get('saramsa_access_token')?.value;

  if (!token) {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = '/login/';
    loginUrl.searchParams.set('redirect', pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|images/|logo/).*)',
  ],
};
