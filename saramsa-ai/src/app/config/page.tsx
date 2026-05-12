'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/useAuth';
import { useDispatch } from 'react-redux';
import type { AppDispatch } from '@/store/store';
import { fetchIntegrationAccounts } from '@/store/features/integrations/integrationsSlice';
import { PlatformSelectionScreen } from '@/components/config/PlatformSelectionScreen';
import { providerConfigScreens } from '@/components/config/providerConfigScreens';
import { forceUnlockBodyScroll } from '@/lib/bodyScrollLock';
import type { WorkProvider } from '@/lib/providers';


export default function ConfigPage() {
  const router = useRouter();
  const {} = useAuth();
  const dispatch = useDispatch<AppDispatch>();
  const [selectedPlatform, setSelectedPlatform] = useState<WorkProvider | null>(null);

  // checkk Fetch integration accounts once at the parent level
  useEffect(() => {
    forceUnlockBodyScroll();
    dispatch(fetchIntegrationAccounts());
  }, [dispatch]);

  const handlePlatformSelect = (platform: WorkProvider) => {
    setSelectedPlatform(platform);
  };

  const handleBackToPlatformSelection = () => {
    setSelectedPlatform(null);
  };

  const handleContinue = async (projectId?: string) => {
    try {
      if (projectId) {
        const { encryptProjectId } = await import('@/lib/encryption');
        const encryptedId = encryptProjectId(projectId);
        router.push(`/projects/${encryptedId}/dashboard/`);
      } else {
        // Fallback to projects page if no project ID
        router.push('/projects/');
      }
    } catch (e) {
      console.error('Navigation error', e);
      // Fallback to projects page on error
      router.push('/projects/');
    }
  };

  const handleSkipConfig = () => {
    router.push('/');
  };

  if (!selectedPlatform) {
    return (
      <div className="h-full overflow-y-auto bg-background z-0">
        <PlatformSelectionScreen 
          onPlatformSelect={handlePlatformSelect}
          onSkipConfig={handleSkipConfig}
        />
      </div>
    );
  }

  const SelectedConfigScreen = providerConfigScreens[selectedPlatform];
  return (
    <div className="h-full overflow-y-auto bg-background">
      <SelectedConfigScreen
        onContinue={handleContinue}
        onBack={handleBackToPlatformSelection}
      />
    </div>
  );
} 
