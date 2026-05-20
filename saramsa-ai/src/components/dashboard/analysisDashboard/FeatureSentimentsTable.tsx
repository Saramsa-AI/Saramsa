'use client';

import { useState } from 'react';
import { CheckCircle2, RefreshCw, AlertCircle, ChevronRight, MessageSquareQuote } from 'lucide-react';
import { Badge } from '../../ui/badge';
import { Button } from '../../ui/button';
import { CompactSentimentBar } from './CompactSentimentBar';

interface FeatureSentiment {
  name: string;
  description: string;
  sentiment: {
    positive: number;
    negative: number;
    neutral: number;
  };
  keywords: string[];
  comment_count?: number;
  isEdited?: boolean;
  sample_comments?: {
    positive?: string[];
    negative?: string[];
    neutral?: string[];
  };
}

interface FeatureSentimentsTableProps {
  features: FeatureSentiment[];
  selectedFeatures: string[];
  onFeatureToggle: (featureName: string) => void;
  onRegenerateAnalysis?: () => void;
  hasEditedFeaturesProp?: boolean;
  hasComments?: boolean;
  // Passed in by Dashboard for downstream context (project-scoped actions
  // and analysis-linked work-item generation). Accepted here so the build
  // type-checks; the component can ignore them unless it needs them.
  projectId?: string | null;
  analysisId?: string | null;
}

export const FeatureSentimentsTable = ({
  features,
  selectedFeatures,
  onFeatureToggle,
  onRegenerateAnalysis,
  hasEditedFeaturesProp,
  hasComments
}: FeatureSentimentsTableProps) => {
  const [expandedFeatures, setExpandedFeatures] = useState<Set<string>>(new Set());

  const toggleExpand = (name: string) => {
    setExpandedFeatures(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  if (!features || features.length === 0) {
    return (
      <div className="space-y-4">
        <h3 className="text-lg font-semibold text-foreground">
          Feature Level Sentiments
        </h3>
        <div className="text-center py-8 border border-dashed border-border/60 rounded-2xl bg-background/60">
          <div className="w-14 h-14 mx-auto mb-4 bg-secondary/60 rounded-full flex items-center justify-center">
            <RefreshCw className="w-7 h-7 text-muted-foreground" />
          </div>
          <p className="text-muted-foreground">
            No feature sentiment data available.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold text-foreground">
            Feature Level Sentiments
          </h3>
          <div className="w-2 h-2 bg-saramsa-brand rounded-full animate-pulse"></div>
        </div>
        {hasEditedFeaturesProp && onRegenerateAnalysis && (
          <Button
            onClick={onRegenerateAnalysis}
            variant="outline"
            className="border-border/70 text-foreground hover:bg-secondary/60"
          >
            <RefreshCw className="w-4 h-4 mr-2" />
            Regenerate
          </Button>
        )}
        {hasEditedFeaturesProp && !hasComments && (
          <div className="flex items-center gap-2 text-saramsa-gradient-to text-sm">
            <AlertCircle className="w-4 h-4" />
            <span>Comments not available for regeneration</span>
          </div>
        )}
      </div>

      {/* Accordion Cards */}
      <div className="space-y-2">
        {features.map((feature, index) => {
          const isExpanded = expandedFeatures.has(feature.name);
          const isSelected = selectedFeatures.includes(feature.name);
          const hasPositive = (feature.sample_comments?.positive?.length ?? 0) > 0;
          const hasNegative = (feature.sample_comments?.negative?.length ?? 0) > 0;
          const hasEvidence = hasPositive || hasNegative;

          return (
            <div
              key={index}
              className={`rounded-xl border transition-all duration-200 ${
                isSelected
                  ? 'border-saramsa-brand/30 bg-saramsa-brand/5'
                  : 'border-border/60 bg-card/40'
              }`}
            >
              {/* Collapsed Header Row */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none"
                onClick={() => toggleExpand(feature.name)}
              >
                {/* Expand Arrow */}
                <ChevronRight
                  className={`w-4 h-4 text-muted-foreground shrink-0 transition-transform duration-200 ${
                    isExpanded ? 'rotate-90' : ''
                  }`}
                />

                {/* Select Checkbox */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onFeatureToggle(feature.name);
                  }}
                  className={`w-4 h-4 rounded-full border-2 shrink-0 transition-all duration-200 flex items-center justify-center ${
                    isSelected
                      ? 'bg-foreground border-foreground'
                      : 'border-border/70 hover:border-saramsa-brand/50'
                  }`}
                >
                  {isSelected && <CheckCircle2 className="w-3 h-3 text-background" />}
                </button>

                {/* Feature Name */}
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-foreground truncate">
                    {feature.name}
                  </span>
                  {feature.comment_count != null && (
                    <span className="text-xs text-muted-foreground ml-1.5">
                      ({feature.comment_count})
                    </span>
                  )}
                  {feature.isEdited && (
                    <Badge className="ml-2 bg-secondary/70 text-foreground text-[10px] py-0">
                      Edited
                    </Badge>
                  )}
                </div>

                {/* Evidence indicator */}
                {hasEvidence && !isExpanded && (
                  <MessageSquareQuote className="w-3.5 h-3.5 text-muted-foreground/50 shrink-0" />
                )}

                {/* Sentiment Bar */}
                <div className="shrink-0">
                  <CompactSentimentBar
                    positive={feature.sentiment.positive}
                    negative={feature.sentiment.negative}
                    neutral={feature.sentiment.neutral}
                  />
                </div>
              </div>

              {/* Expanded Content */}
              {isExpanded && (
                <div className="px-4 pb-4 pt-1 border-t border-border/40 space-y-3">
                  {/* Description */}
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {feature.description}
                  </p>

                  {/* Keywords */}
                  {feature.keywords && feature.keywords.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {feature.keywords.map((keyword, i) => (
                        <span
                          key={i}
                          className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-secondary/70 text-foreground"
                        >
                          {keyword}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Evidence Comments — two columns */}
                  {hasEvidence && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
                      {hasPositive && (
                        <div className="space-y-1.5">
                          <p className="text-xs font-semibold text-saramsa-brand">
                            Positive ({feature.sample_comments!.positive!.length})
                          </p>
                          <div className="space-y-1 max-h-[160px] overflow-y-auto pr-1">
                            {feature.sample_comments!.positive!.slice(0, 10).map((c, i) => (
                              <p
                                key={`pos-${i}`}
                                className="text-xs text-foreground/80 pl-2 border-l-2 border-saramsa-brand/40 leading-relaxed"
                              >
                                {c}
                              </p>
                            ))}
                          </div>
                        </div>
                      )}
                      {hasNegative && (
                        <div className="space-y-1.5">
                          <p className="text-xs font-semibold text-saramsa-gradient-to">
                            Negative ({feature.sample_comments!.negative!.length})
                          </p>
                          <div className="space-y-1 max-h-[160px] overflow-y-auto pr-1">
                            {feature.sample_comments!.negative!.slice(0, 10).map((c, i) => (
                              <p
                                key={`neg-${i}`}
                                className="text-xs text-foreground/80 pl-2 border-l-2 border-saramsa-gradient-to/40 leading-relaxed"
                              >
                                {c}
                              </p>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Selection Status */}
      {selectedFeatures.length > 0 && (
        <div className="p-3 bg-secondary/60 rounded-xl border border-border/70">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 bg-foreground rounded-full flex items-center justify-center shrink-0">
              <CheckCircle2 className="w-3 h-3 text-background" />
            </div>
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{selectedFeatures.length} selected</span> for interactive charts: {selectedFeatures.join(', ')}
            </p>
          </div>
        </div>
      )}
    </div>
  );
};


