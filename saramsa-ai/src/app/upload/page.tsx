'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function UploadPage() {
  const router = useRouter();
  useEffect(() => {
    // `/dashboard/` does not exist (dashboards live at /projects/[id]/dashboard).
    // Send users to the project picker instead of a guaranteed 404.
    router.replace('/projects/');
  }, [router]);
  return null;
}