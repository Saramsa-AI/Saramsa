'use client';

import { useMemo } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  Label,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';

interface FeatureSentiment {
  name: string;
  positive: number;
  negative: number;
  neutral: number;
  comment_count?: number;
}

interface SentimentData {
  name: string;
  value: number;
  color?: string;
  [key: string]: string | number | undefined;
}

interface SentimentChartsProps {
  featureSentimentData: FeatureSentiment[];
  sentimentData: SentimentData[];
  selectedFeatures?: string[];
}

const COLORS = {
  positive: 'var(--sentiment-positive)',
  negative: 'var(--sentiment-negative)',
  neutral: 'var(--sentiment-neutral)',
} as const;

const SENTIMENT_COLOR_MAP: Record<string, string> = {
  Positive: COLORS.positive,
  Negative: COLORS.negative,
  Neutral: COLORS.neutral,
};

interface CustomTooltipPayload {
  color?: string;
  name?: string | number;
  value?: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: CustomTooltipPayload[];
  label?: string | number;
  isPercentage?: boolean;
}

const CustomTooltip = ({ active, payload, label, isPercentage = false }: CustomTooltipProps) => {
  if (!active || !payload || payload.length === 0) return null;

  const total = isPercentage
    ? payload.reduce((sum, entry) => sum + (typeof entry.value === 'number' ? entry.value : 0), 0)
    : 0;

  return (
    <div className="bg-background/95 border border-border/70 rounded-xl p-4 shadow-md">
      {label !== undefined && (
        <p className="text-foreground font-medium mb-2 whitespace-nowrap">
          {label}
        </p>
      )}
      {payload.map((entry, index) => {
        const value = typeof entry.value === 'number' ? entry.value : 0;
        const percentage = isPercentage && total > 0 ? ((value / total) * 100).toFixed(1) : null;

        return (
          <div key={index} className="flex items-center gap-2 text-sm">
            <div
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: entry.color ?? 'var(--muted-foreground)' }}
            />
            <span className="text-muted-foreground">{entry.name}:</span>
            <span className="text-foreground font-medium">
              {Number.isInteger(value) ? value : value.toFixed(2)}
              {percentage && ` (${percentage}%)`}
            </span>
          </div>
        );
      })}
    </div>
  );
};

export function SentimentCharts({
  featureSentimentData,
  sentimentData,
  selectedFeatures = [],
}: SentimentChartsProps) {
  const aggregatedSentimentData = useMemo(() => {
    if (selectedFeatures.length > 0) {
      const filtered = featureSentimentData.filter((feature) =>
        selectedFeatures.includes(feature.name)
      );

      if (!filtered.length) return sentimentData;

      // Each feature's positive/negative/neutral are percentages that already
      // sum to ~100. Naively summing them across N features gives ~100 × N and
      // an unweighted mix. Instead, weight by comment volume: convert each
      // feature's % back to counts, sum, then recompute percentages — so the
      // three values sum to ~100% and reflect true overall sentiment.
      const totalComments = filtered.reduce((s, f) => s + (f.comment_count ?? 0), 0);

      let totalPositive: number;
      let totalNegative: number;
      let totalNeutral: number;

      if (totalComments > 0) {
        const posCount = filtered.reduce((s, f) => s + (f.positive / 100) * (f.comment_count ?? 0), 0);
        const negCount = filtered.reduce((s, f) => s + (f.negative / 100) * (f.comment_count ?? 0), 0);
        const neuCount = filtered.reduce((s, f) => s + (f.neutral / 100) * (f.comment_count ?? 0), 0);
        totalPositive = (posCount / totalComments) * 100;
        totalNegative = (negCount / totalComments) * 100;
        totalNeutral = (neuCount / totalComments) * 100;
      } else {
        // No comment counts available — fall back to a simple average.
        const n = filtered.length;
        totalPositive = filtered.reduce((s, f) => s + f.positive, 0) / n;
        totalNegative = filtered.reduce((s, f) => s + f.negative, 0) / n;
        totalNeutral = filtered.reduce((s, f) => s + f.neutral, 0) / n;
      }

      const base = [
        { name: 'Positive', value: totalPositive },
        { name: 'Negative', value: totalNegative },
        { name: 'Neutral', value: totalNeutral },
      ];

      return base.map((item) => ({
        ...item,
        color: SENTIMENT_COLOR_MAP[item.name] ?? COLORS.neutral,
      }));
    }

    return sentimentData.map((item) => ({
      ...item,
      color: SENTIMENT_COLOR_MAP[item.name] ?? COLORS.neutral,
    }));
  }, [selectedFeatures, featureSentimentData, sentimentData]);

  const totalSentiment = useMemo(() => {
    const total = aggregatedSentimentData.reduce((sum, item) => sum + item.value, 0);
    return Number.isInteger(total) ? total : parseFloat(total.toFixed(2));
  }, [aggregatedSentimentData]);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
        {/* Feature Sentiment vertical grouped bar chart */}
        <div className="bg-card/80 rounded-2xl border border-border/60 overflow-hidden">
          <div className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-foreground">
                Feature Sentiment Distribution
              </h3>
              <div className="flex items-center gap-4">
                {[
                  { label: 'Positive', color: COLORS.positive },
                  { label: 'Negative', color: COLORS.negative },
                  { label: 'Neutral', color: COLORS.neutral },
                ].map((item) => (
                  <div key={item.label} className="flex items-center gap-1.5">
                    <div
                      className="w-2.5 h-2.5 rounded-sm"
                      style={{ backgroundColor: item.color }}
                    />
                    <span className="text-xs text-muted-foreground">
                      {item.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="h-[380px] min-h-[300px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={featureSentimentData}
                  margin={{ top: 12, right: 16, left: 8, bottom: featureSentimentData.length > 4 ? 56 : 28 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border)"
                    opacity={0.2}
                    vertical={false}
                  />
                  <XAxis
                    type="category"
                    dataKey="name"
                    tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                    stroke="var(--border)"
                    interval={0}
                    angle={featureSentimentData.length > 4 ? -32 : 0}
                    textAnchor={featureSentimentData.length > 4 ? 'end' : 'middle'}
                    height={featureSentimentData.length > 4 ? 72 : undefined}
                  />
                  <YAxis
                    type="number"
                    tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                    stroke="var(--border)"
                    tickFormatter={(v) => (Number.isInteger(v) ? `${v}%` : `${v.toFixed(1)}%`)}
                    width={48}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Bar
                    dataKey="positive"
                    fill={COLORS.positive}
                    name="Positive"
                    radius={[4, 4, 0, 0]}
                    animationDuration={800}
                    maxBarSize={28}
                  />
                  <Bar
                    dataKey="negative"
                    fill={COLORS.negative}
                    name="Negative"
                    radius={[4, 4, 0, 0]}
                    animationDuration={800}
                    maxBarSize={28}
                  />
                  <Bar
                    dataKey="neutral"
                    fill={COLORS.neutral}
                    name="Neutral"
                    radius={[4, 4, 0, 0]}
                    animationDuration={800}
                    maxBarSize={28}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Pie Chart */}
        <div className="bg-card/80 rounded-2xl border border-border/60 overflow-hidden flex flex-col">
          <div className="p-6 flex flex-col flex-1">
            <h3 className="text-lg font-semibold text-foreground mb-4">
              {selectedFeatures.length === 0
                ? 'Overall Sentiment Distribution'
                : `Sentiment Distribution (${selectedFeatures.length} Feature${
                    selectedFeatures.length > 1 ? 's' : ''
                  })`}
            </h3>

            <div className="flex-1 min-h-[200px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={aggregatedSentimentData}
                    cx="50%"
                    cy="50%"
                    innerRadius={65}
                    outerRadius={115}
                    paddingAngle={2}
                    dataKey="value"
                    animationDuration={800}
                  >
                    {aggregatedSentimentData.map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={entry.color}
                        stroke={entry.color}
                        strokeWidth={2}
                      />
                    ))}
                    <Label
                      content={({ viewBox }) => {
                        if (viewBox && 'cx' in viewBox && 'cy' in viewBox) {
                          return (
                            <text
                              x={viewBox.cx}
                              y={viewBox.cy}
                              textAnchor="middle"
                              dominantBaseline="middle"
                            >
                              <tspan
                                x={viewBox.cx}
                                y={viewBox.cy}
                                className="fill-foreground"
                                style={{ fontSize: '24px', fontWeight: 700 }}
                              >
                                {totalSentiment}
                              </tspan>
                              <tspan
                                x={viewBox.cx}
                                y={(viewBox.cy || 0) + 26}
                                className="fill-muted-foreground"
                                style={{ fontSize: '14px' }}
                              >
                                Total
                              </tspan>
                            </text>
                          );
                        }
                        return null;
                      }}
                    />
                  </Pie>
                  <Tooltip content={<CustomTooltip isPercentage={true} />} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            {/* Legend below the chart */}
            <div className="flex items-center justify-center gap-4 mt-3">
              {aggregatedSentimentData.map((entry) => (
                <div key={entry.name} className="flex items-center gap-1.5">
                  <div
                    className="w-3 h-3 rounded-sm"
                    style={{ backgroundColor: entry.color }}
                  />
                  <span className="text-sm text-muted-foreground">
                    {entry.name}
                  </span>
                </div>
              ))}
            </div>

            {selectedFeatures.length > 0 && (
              <div className="mt-4 p-3 bg-secondary/50 rounded-xl border border-border/60">
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium">Interactive Mode:</span>{' '}
                  Chart shows aggregated sentiment for selected features only.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
