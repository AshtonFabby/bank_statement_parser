"use client";

import { useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

export default function Home() {
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    setError(null);

    const droppedFiles = Array.from(e.dataTransfer.files).filter(
      (file) => file.type === "application/pdf"
    );

    if (droppedFiles.length === 0) {
      setError("Please drop PDF files only");
      return;
    }

    setFiles((prev) => [...prev, ...droppedFiles]);
  }, []);

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setError(null);
      if (e.target.files) {
        const selectedFiles = Array.from(e.target.files);
        setFiles((prev) => [...prev, ...selectedFiles]);
      }
    },
    []
  );

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleUpload = async () => {
    if (files.length === 0) return;

    setIsUploading(true);
    setProgress(10);
    setError(null);

    const formData = new FormData();
    files.forEach((file) => {
      formData.append("files", file);
    });

    try {
      setProgress(30);

      const response = await fetch("http://localhost:8000/parse", {
        method: "POST",
        body: formData,
      });

      setProgress(70);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Failed to process files");
      }

      setProgress(90);

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "bank_statements.zip";
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);

      setProgress(100);
      setFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsUploading(false);
      setProgress(0);
    }
  };

  return (
    <div className="min-h-screen bg-background p-8">
      <div className="mx-auto max-w-2xl">
        <h1 className="mb-8 text-center text-3xl font-bold">
          Bank Statement Parser
        </h1>

        <Card>
          <CardHeader>
            <CardTitle>Upload Bank Statements</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`flex min-h-[200px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors ${
                isDragging
                  ? "border-primary bg-primary/5"
                  : "border-muted-foreground/25 hover:border-primary/50"
              }`}
              onClick={() => document.getElementById("file-input")?.click()}
            >
              <svg
                className="mb-4 h-12 w-12 text-muted-foreground"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                />
              </svg>
              <p className="mb-2 text-lg font-medium">
                Drag and drop PDF files here
              </p>
              <p className="text-sm text-muted-foreground">
                or click to browse
              </p>
              <input
                id="file-input"
                type="file"
                accept=".pdf"
                multiple
                onChange={handleFileSelect}
                className="hidden"
              />
            </div>

            {files.length > 0 && (
              <div className="space-y-2">
                <h3 className="font-medium">Selected Files:</h3>
                <ul className="space-y-1">
                  {files.map((file, index) => (
                    <li
                      key={index}
                      className="flex items-center justify-between rounded bg-muted px-3 py-2"
                    >
                      <span className="truncate text-sm">{file.name}</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          removeFile(index);
                        }}
                        className="ml-2 text-muted-foreground hover:text-destructive"
                        disabled={isUploading}
                      >
                        <svg
                          className="h-4 w-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M6 18L18 6M6 6l12 12"
                          />
                        </svg>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {error && (
              <div className="rounded bg-destructive/10 px-4 py-2 text-sm text-destructive">
                {error}
              </div>
            )}

            {isUploading && (
              <div className="space-y-2">
                <Progress value={progress} />
                <p className="text-center text-sm text-muted-foreground">
                  Processing...
                </p>
              </div>
            )}

            <Button
              onClick={handleUpload}
              disabled={files.length === 0 || isUploading}
              className="w-full"
            >
              {isUploading ? "Processing..." : "Upload and Process"}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
