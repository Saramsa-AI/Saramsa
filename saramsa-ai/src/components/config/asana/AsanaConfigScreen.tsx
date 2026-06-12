'use client';

import { motion } from 'framer-motion';
import { AsanaIntegrationForm } from './AsanaIntegrationForm';

interface AsanaConfigScreenProps {
  onContinue: (projectId: string) => void;
  onBack: () => void;
}

export function AsanaConfigScreen({ onContinue, onBack }: AsanaConfigScreenProps) {
  return (
    <div className="h-full overflow-y-auto bg-background">
      <div className="min-h-full py-12 lg:py-16">
        <div className="mx-auto max-w-6xl space-y-10 px-4 sm:px-6 lg:px-8">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: 'easeOut' }}
            className="space-y-4 text-center lg:text-left"
          >
            <p className="text-xs uppercase tracking-widest text-muted-foreground">Asana Integration</p>
            <h1 className="text-3xl font-semibold text-foreground sm:text-4xl">
              Connect your Asana workspace and bring projects into Saramsa.ai
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground sm:text-base">
              Link one Asana workspace with a personal access token, choose the customer-facing project you want Saramsa to mirror, and keep insight triage connected to your task workflow.
            </p>
          </motion.div>

          <div className="grid items-start gap-10 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
            <motion.aside
              initial={{ opacity: 0, x: -30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.7, ease: 'easeOut' }}
              className="rounded-3xl bg-gradient-to-br from-saramsa-brand/20 via-saramsa-gradient-to/15 to-transparent p-6 shadow-lg ring-1 ring-saramsa-brand/20 backdrop-blur-sm sm:p-8"
            >
              <div className="space-y-6">
                <div className="space-y-2">
                  <h2 className="text-xl font-semibold text-foreground">Why link Asana?</h2>
                  <p className="text-sm text-muted-foreground">
                    Turn high-signal insights into tracked Asana tasks without forcing your team to work outside its existing planning workflow.
                  </p>
                </div>
                <div className="space-y-4">
                  {[
                    {
                      title: 'Workspace-scoped access',
                      description: 'Connect one workspace at a time so project imports stay explicit and easy to audit.',
                    },
                    {
                      title: 'Project-level linkage',
                      description: 'Choose the exact Asana project Saramsa should map to before any tasks are pushed.',
                    },
                    {
                      title: 'One-way push',
                      description: 'Saramsa pushes insights and work items into Asana; changes you make in Asana stay in Asana.',
                    },
                  ].map((item) => (
                    <div key={item.title} className="rounded-2xl border border-border/60 bg-card/70 p-4">
                      <p className="text-sm font-medium text-foreground">{item.title}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{item.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            </motion.aside>

            <motion.section
              initial={{ opacity: 0, x: 30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.7, ease: 'easeOut' }}
              className="w-full"
            >
              <AsanaIntegrationForm onContinue={onContinue} onBack={onBack} />
            </motion.section>
          </div>
        </div>
      </div>
    </div>
  );
}
