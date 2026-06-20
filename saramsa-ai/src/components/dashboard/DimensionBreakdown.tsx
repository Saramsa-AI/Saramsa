'use client';

import { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Users, BarChart3 } from 'lucide-react';
import { apiRequest } from '@/lib/apiRequest';
import { Badge } from '@/components/ui/badge';

interface SegmentData {
  comment_count: number;
  comment_indices?: number[];
  features?: any[];
}

interface BreakdownData {
  group_by: string;
  segments: Record<string, SegmentData>;
  total_segments: number;
}

interface DimensionBreakdownProps {
  projectId: string;
  analysisId: string;
  featureName?: string;
  className?: string;
}

export function DimensionBreakdown({
  projectId,
  analysisId,
  featureName,
  className = '',
}: DimensionBreakdownProps) {
  const [breakdowns, setBreakdowns] = useState<Record<string, BreakdownData>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDimension, setSelectedDimension] = useState<string | null>(null);

  // Fetch breakdowns for common dimensions
  useEffect(() => {
    if (!projectId || !analysisId) return;

    const fetchBreakdowns = async () => {
      try {
        setLoading(true);

        // Try to fetch breakdowns for common dimensions
        const dimensions = ['platform', 'persona', 'rating', 'subscription_type'];
        const results: Record<string, BreakdownData> = {};

        for (const dimension of dimensions) {
          try {
            const response = await apiRequest(
              'GET',
              `/feedback/insights/breakdown/?project_id=${projectId}&analysis_id=${analysisId}&group_by=${dimension}`
            );

            if (response.data && response.data.data && response.data.data.total_segments > 0) {
              results[dimension] = response.data.data;
            }
          } catch (err) {
            // Dimension might not exist, skip silently
            console.debug(`Dimension ${dimension} not available`);
          }
        }

        setBreakdowns(results);

        // Auto-select first available dimension
        const firstDim = Object.keys(results)[0];
        if (firstDim) {
          setSelectedDimension(firstDim);
        }
      } catch (err) {
        setError('Failed to load dimension breakdowns');
        console.error('Error fetching breakdowns:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchBreakdowns();
  }, [projectId, analysisId]);

  if (loading) {
    return (
      <div className={`animate-pulse ${className}`}>
        <div className="h-4 bg-muted rounded w-32 mb-2"></div>
        <div className="space-y-2">
          <div className="h-8 bg-muted rounded"></div>
          <div className="h-8 bg-muted rounded"></div>
        </div>
      </div>
    );
  }

  if (error || Object.keys(breakdowns).length === 0) {
    return null; // Silently hide if no breakdowns available
  }

  const selectedBreakdown = selectedDimension ? breakdowns[selectedDimension] : null;
  const dimensionKeys = Object.keys(breakdowns);

  const formatDimensionLabel = (key: string): string => {
    return key
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  const getSentimentColor = (count: number, total: number): string => {
    const percentage = total > 0 ? (count / total) * 100 : 0;
    if (percentage >= 60) return 'text-green-600 dark:text-green-400';
    if (percentage >= 40) return 'text-yellow-600 dark:text-yellow-400';
    return 'text-red-600 dark:text-red-400';
  };

  return (
    <div className={`space-y-3 ${className}`}>
      {/* Dimension Selector */}
      <div className="flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-muted-foreground" />
        <span className="text-xs font-medium text-muted-foreground">Group by:</span>
        <div className="flex gap-1">
          {dimensionKeys.map((dim) => (
            <button
              key={dim}
              onClick={() => setSelectedDimension(dim)}
              className={`px-2 py-1 text-xs rounded-md transition-colors ${
                selectedDimension === dim
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-secondary/50 text-muted-foreground hover:bg-secondary'
              }`}
            >
              {formatDimensionLabel(dim)}
            </button>
          ))}
        </div>
      </div>

      {/* Breakdown Display */}
      {selectedBreakdown && (
        <div className="space-y-1.5">
          {Object.entries(selectedBreakdown.segments)
            .sort(([, a], [, b]) => b.comment_count - a.comment_count)
            .slice(0, 5) // Show top 5 segments
            .map(([segmentName, segmentData]) => {
              const totalComments = Object.values(selectedBreakdown.segments).reduce(
                (sum, seg) => sum + seg.comment_count,
                0
              );
              const percentage =
                totalComments > 0
                  ? (segmentData.comment_count / totalComments) * 100
                  : 0;

              return (
                <div
                  key={segmentName}
                  className="flex items-center gap-2 p-2 rounded-lg bg-secondary/30 hover:bg-secondary/50 transition-colors"
                >
                  {/* Segment Name */}
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-medium text-foreground truncate block">
                      {segmentName}
                    </span>
                  </div>

                  {/* Count Badge */}
                  <Badge
                    variant="secondary"
                    className="text-xs px-2 py-0 shrink-0"
                  >
                    <Users className="w-3 h-3 mr-1" />
                    {segmentData.comment_count}
                  </Badge>

                  {/* Percentage Bar */}
                  <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden shrink-0">
                    <div
                      className="h-full bg-primary transition-all duration-300"
                      style={{ width: `${percentage}%` }}
                    />
                  </div>

                  {/* Percentage Text */}
                  <span
                    className={`text-xs font-medium w-10 text-right shrink-0 ${getSentimentColor(
                      segmentData.comment_count,
                      totalComments
                    )}`}
                  >
                    {percentage.toFixed(0)}%
                  </span>
                </div>
              );
            })}
        </div>
      )}

      {/* Summary */}
      {selectedBreakdown && (
        <div className="flex items-center justify-between text-xs text-muted-foreground pt-1 border-t border-border/40">
          <span>
            {selectedBreakdown.total_segments} {formatDimensionLabel(selectedDimension!)} segments
          </span>
          <span>
            Total:{' '}
            {Object.values(selectedBreakdown.segments).reduce(
              (sum, seg) => sum + seg.comment_count,
              0
            )}{' '}
            comments
          </span>
        </div>
      )}
    </div>
  );
}
