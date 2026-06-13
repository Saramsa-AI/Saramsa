'use client';

import { AlertCircle } from 'lucide-react';
import { MetricsCards } from './MetricsCards';
import { FeatureSentimentsTable } from '../../dashboard/analysisDashboard/FeatureSentimentsTable';
import { SentimentCharts } from '../../dashboard/analysisDashboard/SentimentCharts';
import { KeywordCloud } from './KeywordCloud';
import { AdvancedWordCloud } from './AdvancedWordCloud';

interface LocalFeatureSentiment {
  name: string;
  description: string;
  sentiment: { positive: number; negative: number; neutral: number };
  keywords: string[];
  comment_count?: number;
  isEdited?: boolean;
  sample_comments?: { positive?: string[]; negative?: string[]; neutral?: string[] };
}

interface AnalysisProgressUi {
  label: string;
  width: string;
  tone: string;
  text: string;
}

export interface InsightsPanelProps {
  // Layout / loading state
  isSwitchingAnalysis: boolean;
  isTaskViewLoading: boolean;
  analysisProgressUi: AnalysisProgressUi | null;

  // Analysis presence
  hasAnalysisResults: boolean;
  isAnalyzing: boolean;
  selectedAnalysisId: string | null;

  // Data
  metrics: any;
  transformedFeatures: LocalFeatureSentiment[];
  selectedFeatures: string[];
  setSelectedFeatures: React.Dispatch<React.SetStateAction<string[]>>;
  handleRegenerateAnalysis: () => Promise<void>;
  editedKeywords: Record<string, string[]>;
  loadedComments: string[] | null;
  currentProjectId: string;
  latestAnalysis: any;
  featureSentimentData: any[];
  sentimentData: any[];
  wordCloudView: 'split' | 'advanced';
  activeAnalysisData: any;
}

/**
 * Renders the "Insights" tab body of the Dashboard.
 *
 * The component is intentionally "dumb": no Redux selectors, no router
 * awareness, no async fetches. All side effects stay in Dashboard.tsx and
 * required state is passed as explicit props.
 */
export function InsightsPanel(props: InsightsPanelProps) {
  const {
    isSwitchingAnalysis,
    isTaskViewLoading,
    analysisProgressUi,
    hasAnalysisResults,
    isAnalyzing,
    selectedAnalysisId,
    metrics,
    transformedFeatures,
    selectedFeatures,
    setSelectedFeatures,
    handleRegenerateAnalysis,
    editedKeywords,
    loadedComments,
    currentProjectId,
    latestAnalysis,
    featureSentimentData,
    sentimentData,
    wordCloudView,
    activeAnalysisData,
  } = props;

  return (
    <div
      id="panel-insights"
      role="tabpanel"
      aria-labelledby="tab-insights"
      className={`space-y-6 transition-opacity duration-300 ${isSwitchingAnalysis ? 'opacity-50' : 'opacity-100'}`}
    >
      {isTaskViewLoading ? (
        <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-foreground">
                {isSwitchingAnalysis ? 'Loading analysis...' : 'Preparing fresh analysis'}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {isSwitchingAnalysis
                  ? 'Fetching selected analysis data and rebuilding visualizations.'
                  : 'Fetching latest run data and rebuilding charts.'}
              </p>
            </div>
            <span className="inline-flex items-center rounded-full border border-orange-400/30 bg-orange-500/10 px-3 py-1 text-xs font-medium text-orange-600 dark:text-orange-400">
              {analysisProgressUi?.label || (isSwitchingAnalysis ? 'Loading' : 'Processing')}
            </span>
          </div>
          <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="h-20 rounded-xl border border-border/60 bg-secondary/40 animate-pulse" />
            <div className="h-20 rounded-xl border border-border/60 bg-secondary/40 animate-pulse" />
            <div className="h-20 rounded-xl border border-border/60 bg-secondary/40 animate-pulse" />
          </div>
          <div className="mt-4 space-y-3">
            <div className="h-4 w-40 rounded bg-secondary/50 animate-pulse" />
            <div className="h-36 rounded-xl border border-border/60 bg-secondary/30 animate-pulse" />
          </div>
        </div>
      ) : !hasAnalysisResults && !isAnalyzing && selectedAnalysisId ? (
        <div className="bg-card/80 rounded-2xl border border-border/60 p-8 text-center">
          <AlertCircle className="w-12 h-12 text-muted-foreground/50 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-foreground mb-2">
            No Analysis Data Available
          </h3>
          <p className="text-sm text-muted-foreground mb-4">
            The selected analysis could not be loaded or contains no data.
          </p>
          <p className="text-xs text-muted-foreground">
            Try selecting a different analysis or upload new feedback data.
          </p>
        </div>
      ) : (
        <>
          {hasAnalysisResults && <MetricsCards metrics={metrics} />}

          {hasAnalysisResults && (
            <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
              <FeatureSentimentsTable
                features={transformedFeatures}
                selectedFeatures={selectedFeatures}
                onFeatureToggle={(featureName) => {
                  setSelectedFeatures(prev =>
                    prev.includes(featureName)
                      ? prev.filter(name => name !== featureName)
                      : [...prev, featureName]
                  );
                }}
                onRegenerateAnalysis={handleRegenerateAnalysis}
                hasEditedFeaturesProp={Object.keys(editedKeywords).length > 0}
                hasComments={!!loadedComments && loadedComments.length > 0}
                projectId={currentProjectId}
                analysisId={selectedAnalysisId ?? latestAnalysis?.analysis?.id ?? undefined}
              />
            </div>
          )}
        </>
      )}

      {hasAnalysisResults && !isTaskViewLoading && (
        <SentimentCharts
          featureSentimentData={featureSentimentData}
          sentimentData={sentimentData}
          selectedFeatures={selectedFeatures}
        />
      )}

      {hasAnalysisResults && !isTaskViewLoading && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-foreground">Word Cloud Analysis</h3>
          </div>

          {wordCloudView === 'split' ? (
            <KeywordCloud
              positiveKeywords={
                activeAnalysisData?.analysisData?.positive_keywords?.map((word: any) =>
                  typeof word === 'string' ? word : word.keyword || word.text || String(word)
                ) || []
              }
              negativeKeywords={
                activeAnalysisData?.analysisData?.negative_keywords?.map((word: any) =>
                  typeof word === 'string' ? word : word.keyword || word.text || String(word)
                ) || []
              }
            />
          ) : (
            <AdvancedWordCloud
              positiveKeywords={
                activeAnalysisData?.analysisData?.positive_keywords?.map((word: any) =>
                  typeof word === 'string' ? word : word.keyword || word.text || String(word)
                ) || []
              }
              negativeKeywords={
                activeAnalysisData?.analysisData?.negative_keywords?.map((word: any) =>
                  typeof word === 'string' ? word : word.keyword || word.text || String(word)
                ) || []
              }
            />
          )}
        </div>
      )}

      {hasAnalysisResults && !isTaskViewLoading && (
        <div className="text-xs text-muted-foreground/70 text-right">
          Analysis from {(() => {
            const analysisDate = activeAnalysisData?.createdAt;
            const deepAnalysisDate = activeAnalysisData?.deepAnalysis?.generated_at;
            const timestamp = deepAnalysisDate || analysisDate;
            if (timestamp) {
              return new Date(timestamp).toLocaleDateString('en-US', {
                year: 'numeric', month: 'long', day: 'numeric',
                hour: '2-digit', minute: '2-digit',
              });
            }
            return new Date().toLocaleDateString();
          })()}
        </div>
      )}
    </div>
  );
}
