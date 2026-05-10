"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, BarChart3, FileText, FolderOpen, Trash2, MessageSquare, Plus } from 'lucide-react';
import { Button } from "../button";
import { Input } from "../input";

interface UploadPanelProps {
  dbProjectId: string;
  topFile: File | null;
  topError: string | null;
  loadedComments: string[] | null;
  topUploading: boolean;
  integrationsLoading?: boolean;
  slackConnected?: boolean;
  slackDisplayName?: string | null;
  onFileSelect: (file: File | null) => void;
  onAnalyze: () => Promise<void>;
  onCloudConnect: () => void;
  isAnalyzing?: boolean;
}

export function UploadPanel({
  dbProjectId,
  topFile,
  topError,
  loadedComments,
  topUploading,
  integrationsLoading = false,
  slackConnected = false,
  slackDisplayName = null,
  onFileSelect,
  onAnalyze,
  onCloudConnect,
  isAnalyzing = false,
}: UploadPanelProps) {
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Reset file input when topFile becomes null (after analysis completes)
  useEffect(() => {
    if (!topFile && fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, [topFile]);

  const handleFileSelect = (file: File | null) => {
    onFileSelect(file);
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0] ?? null;
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!isDragging) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
  };

  const removeFile = () => {
    onFileSelect(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const canAnalyze = !!topFile;

  return (
    <section className="space-y-5 py-2">
      <div className="flex items-start justify-start">
        <div>
          <h2 className="text-lg text-foreground font-semibold">Feedback Upload</h2>
          <p className="text-xs sm:text-sm text-muted-foreground">
            Choose a CSV, JSON, PDF, TXT, or DOCX file and run analysis.
          </p>
        </div>
      </div>

      <div
        className={`border rounded-2xl px-4 sm:px-5 py-3 sm:py-3.5 bg-background/40 transition-colors ${
          isDragging ? "border-saramsa-brand/50 bg-secondary/60" : "border-border/60"
        }`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        {!topFile ? (
          <div
            className="flex items-center gap-4"
            onClick={() => document.getElementById("file-upload")?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                document.getElementById("file-upload")?.click();
              }
            }}
          >
            <div className="w-9 h-9 rounded-lg border border-border/60 bg-secondary/50 flex items-center justify-center shrink-0">
              <Upload className="w-4 h-4 text-saramsa-brand" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground">No file selected</p>
              <p className="text-xs text-muted-foreground truncate">Drag and drop a file or click Browse</p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {integrationsLoading
                  ? "Checking integrations..."
                  : slackConnected
                  ? `Slack connected${slackDisplayName ? `: ${slackDisplayName}` : ""}`
                  : "Slack not connected"}
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant="outline"
                size="sm"
                className="border-border/70 hover:bg-accent/60 text-foreground"
                onClick={(e) => {
                  e.stopPropagation();
                  onCloudConnect();
                }}
                title={slackConnected ? "Manage Slack integration" : "Connect Slack integration"}
              >
                <Plus className="w-4 h-4" />
                <MessageSquare className="w-4 h-4" />
                {slackConnected ? "Slack" : "Connect Slack"}
              </Button>
              <Button
                variant="saramsa"
                size="sm"
                className="shrink-0"
                onClick={(e) => {
                  e.stopPropagation();
                  document.getElementById("file-upload")?.click();
                }}
              >
                <FolderOpen className="w-4 h-4" />
                Browse
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col sm:flex-row sm:items-center gap-4">
            <div className="w-9 h-9 rounded-lg border border-border/60 bg-secondary/50 flex items-center justify-center shrink-0">
              <FileText className="w-4 h-4 text-saramsa-brand" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{topFile.name}</p>
              <p className="text-xs text-muted-foreground">{(topFile.size / 1024).toFixed(1)} KB</p>
            </div>
            <div className="flex items-center gap-2 sm:ml-auto pt-1 sm:pt-0">
              <Button
                onClick={onAnalyze}
                disabled={topUploading || !canAnalyze}
                size="sm"
                className="bg-foreground text-background hover:bg-foreground/90 disabled:opacity-50 gap-2 px-3"
              >
                <BarChart3 className="w-4 h-4 shrink-0" />
                <span>{topUploading ? "Analyzing..." : "Analyze"}</span>
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="border-border/70 hover:bg-accent/60"
                onClick={removeFile}
                title="Remove file"
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}

        <Input
          id="file-upload"
          type="file"
          ref={fileInputRef}
          accept=".csv,.json,.pdf,.txt,.docx"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            handleFileSelect(f);
          }}
          className="hidden"
        />
      </div>

      {/* Error Display */}
      {topError && (
        <div>
          <div className="p-3 bg-red-50/80 dark:bg-red-900/20 border border-red-200/70 dark:border-red-800/60 rounded-2xl">
            <p className="text-sm text-red-600 dark:text-red-400">{topError}</p>
          </div>
        </div>
      )}
    </section>
  );
}

