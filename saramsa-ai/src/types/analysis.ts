export type SentimentBreakdown = {
  positive: number;
  negative: number;
  neutral: number;
};

export type FeatureSentiment = {
  featureId: string;
  name: string;
  description: string;
  sentiment: SentimentBreakdown;
  keywords: string[];
  comment_count: number;
};

export type EmojiAnalysis = {
  overallsentiment: 'Positive' | 'Negative' | 'Neutral';
  topemojis?: string[];
};

export type ActionItems = {
  featurerequests?: Array<{ feature: string; request: string }>;
  bugs?: Array<{ feature: string; issue: string }>;
  changerequests?: Array<{ feature: string; request: string }>;
};

export type AnalysisData = {
  id: string;
  projectId: string;
  userId: string;
  createdAt: string;
  analysisType: string;
  rawLlm: any;
  insights?: string[];
  dimensions?: Array<Record<string, any>>;
  analysisData: {
    overall: SentimentBreakdown;
    counts: { total: number; positive: number; negative: number; neutral: number };
    features: FeatureSentiment[];
    positive_keywords: Array<{ keyword: string; sentiment: number }>;
    negative_keywords: Array<{ keyword: string; sentiment: number }>;
    pipeline_metadata?: {
      processing_time?: number;
      model_info?: any;
      unmapped_percentage?: number;
      confidence_distribution?: Record<string, number>;
    } | null;
  };
  deepAnalysis?: {
    work_items?: any[];
    work_items_by_feature?: any;
    summary?: any;
    process_template?: string;
    generated_at?: string;
    comments_count?: number;
  };
  analysis_date?: string;
  total_comments_analyzed?: number;
};
