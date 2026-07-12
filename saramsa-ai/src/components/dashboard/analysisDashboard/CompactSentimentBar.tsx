import { motion } from "framer-motion";

interface CompactSentimentBarProps {
  positive: number;
  negative: number;
  neutral: number;
}

export const CompactSentimentBar = ({
  positive,
  negative,
  neutral,
}: CompactSentimentBarProps) => {
  const total = positive + negative + neutral;

  const positivePercent = total > 0 ? (positive / total) * 100 : 0;
  const negativePercent = total > 0 ? (negative / total) * 100 : 0;
  const neutralPercent = total > 0 ? (neutral / total) * 100 : 0;

  const tinySegmentMinWidth = (percent: number) =>
    percent > 0 && percent < 5 ? "2px" : "0px";

  // Native title so the breakdown stays readable inside the scrollable feature
  // list — a custom absolutely-positioned tooltip gets clipped by the list's
  // overflow container.
  const breakdownTitle =
    `Positive ${positivePercent.toFixed(1)}%  ·  ` +
    `Negative ${negativePercent.toFixed(1)}%  ·  ` +
    `Neutral ${neutralPercent.toFixed(1)}%`;

  return (
    <div className="relative cursor-help" title={breakdownTitle}>
      {/* Stacked Capsule Bar */}
      <div className="flex items-center bg-secondary/60 rounded-full h-6 w-20 overflow-hidden border border-border/60">
        {/* Positive segment */}
        {positivePercent > 0 && (
          <motion.div
            className="bg-saramsa-brand/70 h-full"
            initial={{ width: 0 }}
            animate={{ width: `${positivePercent}%` }}
            transition={{ duration: 0.8, delay: 0.1 }}
            style={{ minWidth: tinySegmentMinWidth(positivePercent) }}
          />
        )}

        {/* Negative segment */}
        {negativePercent > 0 && (
          <motion.div
            className="bg-saramsa-gradient-to/70 h-full"
            initial={{ width: 0 }}
            animate={{ width: `${negativePercent}%` }}
            transition={{ duration: 0.8, delay: 0.2 }}
            style={{ minWidth: tinySegmentMinWidth(negativePercent) }}
          />
        )}

        {/* Neutral segment */}
        {neutralPercent > 0 && (
          <motion.div
            className="bg-muted-foreground/50 h-full"
            initial={{ width: 0 }}
            animate={{ width: `${neutralPercent}%` }}
            transition={{ duration: 0.8, delay: 0.3 }}
            style={{ minWidth: tinySegmentMinWidth(neutralPercent) }}
          />
        )}
      </div>

    </div>
  );
};
