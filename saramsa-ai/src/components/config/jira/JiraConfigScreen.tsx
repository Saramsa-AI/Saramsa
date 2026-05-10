'use client';

import { motion } from 'framer-motion';
import { JiraIntegrationForm } from './JiraIntegrationForm';

interface JiraConfigScreenProps {
  onContinue: (projectId: string) => void;
  onBack: () => void;
}

export function JiraConfigScreen({ onContinue, onBack }: JiraConfigScreenProps) {
  return (
    <div className="h-full overflow-y-auto bg-background">
      <div className="min-h-full py-12 lg:py-16">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 space-y-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="space-y-4 text-center lg:text-left"
        >
          <p className="text-xs uppercase tracking-widest text-muted-foreground">
            Jira Integration
          </p>
          <h1 className="text-3xl sm:text-4xl font-semibold text-foreground">
            Connect your Jira workspace and bring projects into Saramsa.ai
          </h1>
          <p className="text-sm sm:text-base text-muted-foreground max-w-2xl lg:max-w-3xl mx-auto lg:mx-0">
            Link your Jira account once, then choose the project you would like Saramsa.ai to use when generating issues from customer feedback. We handle the secure connection and remember your configuration for future sessions.
          </p>
        </motion.div>

          <div className="grid gap-10 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] items-start">
          <motion.aside
            initial={{ opacity: 0, x: -30 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.7, ease: "easeOut" }}
            className="rounded-3xl bg-gradient-to-br from-saramsa-brand/20 via-saramsa-gradient-to/15 to-transparent p-6 sm:p-8 shadow-lg ring-1 ring-saramsa-brand/20 backdrop-blur-sm"
          >
            <div className="space-y-6">
              <div className="space-y-2">
                <h2 className="text-xl font-semibold text-foreground">
                  Why link Jira?
                </h2>
                <p className="text-sm text-muted-foreground">
                  Create issues directly from feedback, synchronize updates, and keep teams aligned without switching tools.
                </p>
              </div>
              <div className="space-y-4">
                {[
                  {
                    title: "Secure authentication",
                    description: "API tokens stay encrypted in storage and never leave your browser without consent."
                  },
                  {
                    title: "Synced project context",
                    description: "Choose the project where Saramsa.ai should publish triaged feedback as issues."
                  },
                  {
                    title: "Reusable integration",
                    description: "We detect existing connections and let you manage multiple Jira accounts with ease."
                  }
                ].map((item) => (
                  <div key={item.title} className="rounded-2xl border border-border/60 bg-card/70 p-4">
                    <p className="text-sm font-medium text-foreground">{item.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.description}
                    </p>
                  </div>
                ))}
              </div>
              <div className="rounded-2xl border border-border/60 bg-secondary/50 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
                  Need help?
                </p>
                <p className="mt-2 text-sm text-foreground">
                  Reach out to your Saramsa.ai admin to enable Jira integrations for your workspace.
                </p>
              </div>
            </div>
          </motion.aside>

          <motion.section
            initial={{ opacity: 0, x: 30 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.7, ease: "easeOut" }}
            className="w-full"
          >
            <JiraIntegrationForm onContinue={onContinue} onBack={onBack} />
          </motion.section>
          </div>
        </div>
      </div>
    </div>
  );
}
