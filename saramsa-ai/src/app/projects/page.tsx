'use client';

import { useEffect, useCallback, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useRouter } from 'next/navigation';
import type { AppDispatch, RootState } from '@/store/store';
import { fetchProjects, markProjectViewed, type Project } from '@/store/features/projects/projectsSlice';
import { ProjectDashboard } from '@/components/ui/dashboard/ProjectDashboard';
import { encryptProjectId } from '@/lib/encryption';

function ProjectsPage() {
  const dispatch = useDispatch<AppDispatch>();
  const router = useRouter();
  const hasFetchedRef = useRef(false);

  useEffect(() => {
    if (hasFetchedRef.current) return;
    hasFetchedRef.current = true;
    
    // Don't fetch here since ProjectDashboard already fetches
    // dispatch(fetchProjects());
  }, [dispatch]);

  const handleGoToProject = useCallback((project: Project) => {
    // Record the view (fire-and-forget) so this project sorts to the top of the
    // list next time. Does not block or affect navigation.
    markProjectViewed(project.id);
    try {
      const encryptedId = encryptProjectId(project.id);
      router.push(`/projects/${encryptedId}/dashboard/`);
    } catch (error) {
      console.error('Failed to navigate to project:', error);
      // Fallback to unencrypted ID if encryption fails
      router.push(`/projects/${project.id}/dashboard/`);
    }
  }, [router]);

  return (
    <div className="min-h-screen bg-background">
      <ProjectDashboard 
        onGoToProject={handleGoToProject}
      />
    </div>
  );
}

export default ProjectsPage;