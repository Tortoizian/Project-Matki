"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  Gavel,
  Loader2,
  Scale,
  ShieldCheck,
  Upload,
  X,
  Calendar,
  Building2,
  FileSearch,
  Brain,
  ArrowLeft,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = "intake" | "processing" | "review" | "success" | "error";

interface PreviewPayload {
  accused_name?: string | null;
  fir_number?: string | null;
  days_in_custody?: number;
  applicable_sections?: string[];
  summary_excerpt?: string;
  confidence_score?: number;
}

interface ReviewResponse {
  status: "awaiting_human_review";
  report: Record<string, unknown>;
  preview: PreviewPayload;
}

interface CourtStatus {
  source?: string;
  cnr?: string;
  status?: string;
  case_number?: string;
  case_stage?: string;
  next_hearing_date?: string;
  last_updated?: string;
  court?: string;
  judge?: string;
}

interface GeneratedResponse {
  status: "generated";
  pdf_path: string;
  download_url: string;
  court_status?: CourtStatus | null;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function MuktiWizard() {
  const [step, setStep] = useState<WizardStep>("intake");
  const [file, setFile] = useState<File | null>(null);
  const [cnr, setCnr] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [reviewData, setReviewData] = useState<ReviewResponse | null>(null);
  const [generated, setGenerated] = useState<GeneratedResponse | null>(null);

  const reset = () => {
    setFile(null);
    setCnr("");
    setReviewData(null);
    setGenerated(null);
    setErrorMsg(null);
    setStep("intake");
  };

  // -------------------- API calls --------------------

  const submitForReview = async () => {
    if (!file) {
      setErrorMsg("Please upload an FIR document image first.");
      return;
    }
    setErrorMsg(null);
    setStep("processing");

    const fd = new FormData();
    fd.append("file", file);
    if (cnr.trim()) fd.append("cnr_number", cnr.trim());
    fd.append("approved", "false");

    try {
      const res = await fetch(`${API_URL}/process-case`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Server returned ${res.status}: ${text.slice(0, 200)}`);
      }
      const data = (await res.json()) as ReviewResponse;
      if (data.status !== "awaiting_human_review") {
        throw new Error(
          `Unexpected status: ${data.status}. Manual entry mode is not yet wired in this UI.`
        );
      }
      setReviewData(data);
      setStep("review");
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Unknown error contacting backend.";
      setErrorMsg(msg);
      setStep("error");
    }
  };

  const approveAndGenerate = async () => {
    if (!reviewData) return;
    setErrorMsg(null);
    setStep("processing");

    const fd = new FormData();
    fd.append("fir_data_json", JSON.stringify(reviewData.report));
    if (cnr.trim()) fd.append("cnr_number", cnr.trim());
    fd.append("approved", "true");

    try {
      const res = await fetch(`${API_URL}/process-case`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Server returned ${res.status}: ${text.slice(0, 200)}`);
      }
      const data = (await res.json()) as GeneratedResponse;
      setGenerated(data);
      setStep("success");
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "PDF generation failed.";
      setErrorMsg(msg);
      setStep("error");
    }
  };

  // -------------------- Render --------------------

  return (
    <main className="min-h-screen bg-black text-gray-200 selection:bg-purple-500/30">
      <div className="fixed inset-0 z-0 bg-[radial-gradient(circle_at_top,_var(--tw-gradient-stops))] from-purple-900/20 via-black to-black"></div>
      
      <div className="relative z-10 flex flex-col min-h-screen">
        <Header />

        <div className="mx-auto w-full max-w-3xl flex-1 px-4 pb-24 pt-8 sm:pt-12">
          <StepIndicator step={step} />

          <div className="mt-8 transition-all duration-500 ease-in-out">
            {step === "intake" && (
              <IntakeStep
                file={file}
                setFile={setFile}
                cnr={cnr}
                setCnr={setCnr}
                onSubmit={submitForReview}
                error={errorMsg}
              />
            )}

            {step === "processing" && <ProcessingStep />}

            {step === "review" && reviewData && (
              <ReviewStep
                preview={reviewData.preview}
                onApprove={approveAndGenerate}
                onCancel={reset}
              />
            )}

            {step === "success" && generated && (
              <SuccessStep generated={generated} onReset={reset} />
            )}

            {step === "error" && (
              <ErrorStep message={errorMsg ?? "Something went wrong."} onReset={reset} />
            )}
          </div>
        </div>

        <Footer />
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header() {
  return (
    <header className="border-b border-white/5 bg-black/40 backdrop-blur-xl">
      <div className="mx-auto flex max-w-3xl items-center gap-4 px-4 py-4">
        <div className="rounded-xl bg-gradient-to-br from-purple-600 to-indigo-600 p-2.5 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)]">
          <Scale className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Project Mukti
            <span className="rounded-full bg-purple-500/20 border border-purple-500/30 px-2 py-0.5 text-[10px] font-medium text-purple-300 uppercase tracking-wider">Beta</span>
          </h1>
          <p className="text-sm text-gray-400 mt-0.5">
            AI-assisted bail eligibility audits
          </p>
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

const STEPS: { key: WizardStep; label: string }[] = [
  { key: "intake", label: "Upload" },
  { key: "processing", label: "Analyze" },
  { key: "review", label: "Review" },
  { key: "success", label: "Generate" },
];

function StepIndicator({ step }: { step: WizardStep }) {
  const activeIdx = STEPS.findIndex((s) => s.key === step);
  return (
    <ol className="flex items-center justify-between gap-2 px-2">
      {STEPS.map((s, i) => {
        const done = i < activeIdx || (step === "success" && i <= activeIdx);
        const active = i === activeIdx;
        return (
          <li key={s.key} className="flex flex-1 items-center gap-3">
            <div
              className={[
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-all duration-500",
                active
                  ? "border-purple-500 bg-purple-500/20 text-purple-200 shadow-[0_0_15px_rgba(168,85,247,0.4)]"
                  : done
                  ? "border-indigo-500/50 bg-indigo-500/20 text-indigo-300"
                  : "border-white/10 bg-white/5 text-gray-500",
              ].join(" ")}
            >
              {done ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
            </div>
            <span
              className={[
                "hidden text-sm font-medium sm:block transition-colors duration-300",
                active ? "text-purple-200" : done ? "text-indigo-300" : "text-gray-500",
              ].join(" ")}
            >
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <div
                className={[
                  "ml-1 h-[2px] flex-1 rounded-full transition-all duration-500",
                  i < activeIdx ? "bg-gradient-to-r from-purple-500 to-indigo-500" : "bg-white/10",
                ].join(" ")}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Intake
// ---------------------------------------------------------------------------

function IntakeStep({
  file,
  setFile,
  cnr,
  setCnr,
  onSubmit,
  error,
}: {
  file: File | null;
  setFile: (f: File | null) => void;
  cnr: string;
  setCnr: (v: string) => void;
  onSubmit: () => void;
  error: string | null;
}) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const f = e.dataTransfer.files?.[0];
      if (f) setFile(f);
    },
    [setFile]
  );

  return (
    <section className="space-y-5 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <Card>
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2 rounded-lg bg-white/5 border border-white/10">
            <FileText className="h-4 w-4 text-purple-400" />
          </div>
          <h2 className="text-lg font-semibold text-white">
            Upload the FIR Document
          </h2>
        </div>
        <p className="mb-6 text-sm text-gray-400 leading-relaxed">
          Provide a clear photo or PDF scan. We run edge-based analysis—your file doesn't persist beyond this session.
        </p>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={[
            "group relative mt-2 flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed p-10 text-center transition-all duration-300",
            dragOver
              ? "border-purple-500 bg-purple-500/10"
              : file
              ? "border-indigo-400 bg-indigo-500/10"
              : "border-white/15 bg-white/5 hover:border-purple-500/50 hover:bg-purple-500/5",
          ].join(" ")}
        >
          <div className="absolute inset-0 bg-gradient-to-br from-purple-500/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100 rounded-xl" />
          
          <input
            ref={inputRef}
            type="file"
            accept="image/*,application/pdf"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <div className="relative z-10 flex items-center gap-4">
              <div className="p-3 bg-indigo-500/20 rounded-full border border-indigo-500/30">
                <CheckCircle2 className="h-8 w-8 text-indigo-400" />
              </div>
              <div className="text-left">
                <p className="font-medium text-white truncate max-w-[200px]">{file.name}</p>
                <p className="text-xs text-indigo-300">
                  {(file.size / 1024).toFixed(1)} KB · click to replace
                </p>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setFile(null);
                }}
                className="ml-2 rounded-full p-1.5 text-gray-400 transition-colors hover:bg-white/10 hover:text-white"
                aria-label="Remove file"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <div className="relative z-10 flex flex-col items-center">
              <div className="mb-4 rounded-full bg-white/5 p-4 ring-1 ring-white/10 transition-all duration-300 group-hover:scale-110 group-hover:bg-purple-500/20 group-hover:ring-purple-500/40">
                <Upload className="h-6 w-6 text-gray-400 group-hover:text-purple-300" />
              </div>
              <p className="text-sm font-medium text-gray-200">
                Drag & drop or <span className="text-purple-400">click to browse</span>
              </p>
              <p className="mt-1.5 text-xs text-gray-500">
                Supports PNG, JPG, and PDF formats
              </p>
            </div>
          )}
        </div>
      </Card>

      <Card>
        <label className="block group">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm font-medium text-gray-200">
              CNR Number <span className="text-gray-500 font-normal">(Optional)</span>
            </span>
          </div>
          <p className="mb-3 text-xs text-gray-400">
            For retrieving live eCourts status (16 characters).
          </p>
          <div className="relative">
            <input
              type="text"
              value={cnr}
              onChange={(e) => setCnr(e.target.value.toUpperCase())}
              maxLength={16}
              placeholder="e.g. MHCC010012345-2024"
              className="w-full rounded-lg border border-white/10 bg-[#0a0a0c] px-4 py-3 text-sm text-white font-mono tracking-wider transition-all placeholder:text-gray-600 focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500/50"
            />
            {cnr && (
              <div className="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-mono text-purple-400">
                {cnr.length}/16
              </div>
            )}
          </div>
        </label>
      </Card>

      {error && <InlineError message={error} />}

      <button
        type="button"
        onClick={onSubmit}
        disabled={!file}
        className="group relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-[#1e1e24] px-6 py-4 text-sm font-medium text-white transition-all hover:bg-[#25252c] disabled:cursor-not-allowed disabled:bg-white/5 disabled:text-gray-500 border border-white/10 hover:border-purple-500/50"
      >
        {!file ? null : (
          <div className="absolute inset-0 bg-gradient-to-r from-purple-600/20 to-indigo-600/20 opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
        )}
        <ShieldCheck className="h-5 w-5 relative z-10" />
        <span className="relative z-10">Start comprehensive analysis</span>
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Processing
// ---------------------------------------------------------------------------

const PROCESSING_STAGES = [
  { icon: FileSearch, label: "Scanning document contents…" },
  { icon: Brain, label: "Correlating legal guidelines…" },
  { icon: Gavel, label: "Computing systemic bail score…" },
];

function ProcessingStep() {
  const [active, setActive] = useState(0);
  useEffect(() => {
    const t = setInterval(
      () => setActive((i) => (i + 1) % PROCESSING_STAGES.length),
      2500
    );
    return () => clearInterval(t);
  }, []);
  return (
    <Card className="animate-in fade-in zoom-in-95 duration-500">
      <div className="flex flex-col items-center gap-8 py-10">
        <div className="relative flex items-center justify-center">
          <div className="absolute inset-0 animate-ping rounded-full bg-purple-500/20" />
          <div className="relative rounded-full bg-[#111116] p-4 ring-1 ring-white/10 flex items-center justify-center shadow-[0_0_30px_rgba(168,85,247,0.3)]">
            <Loader2 className="h-8 w-8 animate-spin text-purple-400" />
          </div>
        </div>
        
        <div className="text-center">
          <h2 className="text-lg font-medium text-white mb-2 tracking-wide">
            Processing Case Protocol
          </h2>
          <p className="text-xs text-gray-500">
            Secure analysis in progress. Please do not close the window.
          </p>
        </div>

        <ul className="w-full space-y-3">
          {PROCESSING_STAGES.map((s, i) => {
            const Icon = s.icon;
            const isActive = i === active;
            const isDone = i < active;
            return (
              <li
                key={s.label}
                className={[
                  "flex items-center gap-4 rounded-xl border px-5 py-3.5 transition-all duration-500",
                  isActive
                    ? "border-purple-500/50 bg-purple-500/10 text-purple-200 shadow-[0_0_15px_rgba(168,85,247,0.1)] translate-x-2"
                    : isDone
                    ? "border-indigo-500/30 bg-indigo-500/5 text-indigo-300"
                    : "border-white/5 bg-transparent text-gray-600",
                ].join(" ")}
              >
                <div className="w-6 flex justify-center shrink-0">
                  {isDone ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : isActive ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <Icon className="h-5 w-5 opacity-50" />
                  )}
                </div>
                <span className="text-sm font-medium">{s.label}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — Review
// ---------------------------------------------------------------------------

function ReviewStep({
  preview,
  onApprove,
  onCancel,
}: {
  preview: PreviewPayload;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const score = Math.max(0, Math.min(100, preview.confidence_score ?? 0));
  const scoreColor =
    score >= 70
      ? "bg-emerald-500"
      : score >= 40
      ? "bg-amber-500"
      : "bg-rose-500";
  const scoreLabel =
    score >= 70 ? "Strong grounds" : score >= 40 ? "Some grounds" : "Weak grounds";

  return (
    <section className="space-y-5 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <Card>
        <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              Auditor Report
            </h2>
            <p className="mt-1 text-sm text-gray-400">
              The AI has prepared a draft. Manual verification is pending.
            </p>
          </div>
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-semibold text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            Awaiting Review
          </span>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Accused" value={preview.accused_name} />
          <Field label="FIR Number" value={preview.fir_number} />
          <Field
            label="Custody Duration"
            value={
              preview.days_in_custody !== undefined
                ? `${preview.days_in_custody} days`
                : null
            }
          />
          <Field
            label="Applied Sections"
            value={
              preview.applicable_sections?.length
                ? preview.applicable_sections.join(", ")
                : null
            }
          />
        </div>

        <div className="mt-6 rounded-lg bg-[#0e0e12] border border-white/5 p-4">
          <div className="mb-2.5 flex items-center justify-between text-sm">
            <span className="font-medium text-gray-300 flex items-center gap-2">
              <Brain className="h-4 w-4 text-purple-400" />
              Algorithmic Confidence
            </span>
            <span className="font-mono text-xs text-gray-400">
              <span className={score >= 70 ? "text-emerald-400" : score >= 40 ? "text-amber-400" : "text-rose-400"}>{scoreLabel}</span> • {score}/100
            </span>
          </div>
          <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-white/10">
            <div
              className={`absolute left-0 top-0 h-full rounded-full transition-all duration-1000 ease-out ${scoreColor}`}
              style={{ width: `${score}%`, boxShadow: `0 0 10px var(--tw-colors-${scoreColor.split('-')[1]}-500)` }}
            />
          </div>
        </div>
      </Card>

      <Card>
        <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-purple-400 mb-3">
          <FileText className="h-4 w-4" />
          Summary Synthesis
        </h3>
        <div className="rounded-lg bg-white/5 border border-white/5 p-4 text-sm leading-relaxed text-gray-300">
          <p className="whitespace-pre-line">
            {preview.summary_excerpt || "No summary synthesis available."}
          </p>
        </div>
      </Card>

      <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 shadow-[0_4px_20px_-5px_rgba(245,158,11,0.1)]">
        <div className="flex gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm text-amber-200/80 leading-relaxed">
            <strong className="text-amber-400 font-medium">Licensed advocate review mandatory.</strong>{" "}
            This system identifies structural components of a bail plea but is not legal counsel. Do not file unverified outputs.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row mt-2">
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-white/10 bg-transparent px-5 py-3.5 text-sm font-medium text-gray-300 transition hover:bg-white/5 hover:text-white"
        >
          <ArrowLeft className="h-4 w-4" />
          Discard Analysis
        </button>
        <button
          type="button"
          onClick={onApprove}
          className="group relative inline-flex flex-1 items-center justify-center gap-2 overflow-hidden rounded-xl border border-purple-500/50 bg-[#161320] px-5 py-3.5 text-sm font-medium text-purple-200 shadow-[0_0_20px_rgba(168,85,247,0.15)] transition-all hover:bg-[#1a1625] hover:shadow-[0_0_25px_rgba(168,85,247,0.3)] hover:text-white"
        >
          <div className="absolute inset-0 bg-gradient-to-r from-purple-600/20 to-indigo-600/20 opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
          <CheckCircle2 className="h-5 w-5 relative z-10" />
          <span className="relative z-10 text-white">Approve & Generate PDF</span>
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — Success
// ---------------------------------------------------------------------------

function SuccessStep({
  generated,
  onReset,
}: {
  generated: GeneratedResponse;
  onReset: () => void;
}) {
  const downloadHref = generated.download_url.startsWith("http")
    ? generated.download_url
    : `${API_URL}${generated.download_url}`;

  const cs = generated.court_status;
  const courtFound = cs && cs.status !== "not_found";

  return (
    <section className="space-y-5 animate-in zoom-in-95 duration-500">
      <Card className="border-emerald-500/20 bg-emerald-500/5">
        <div className="flex flex-col items-center text-center py-4">
          <div className="relative mb-6">
            <div className="absolute inset-0 animate-ping rounded-full bg-emerald-500/30" />
            <div className="relative rounded-full bg-emerald-500/20 p-4 ring-1 ring-emerald-500/40 shadow-[0_0_30px_rgba(16,185,129,0.3)]">
              <CheckCircle2 className="h-10 w-10 text-emerald-400" />
            </div>
          </div>
          <h2 className="text-xl font-semibold text-white tracking-tight">
            Document Generation Complete
          </h2>
          <p className="mt-2.5 max-w-sm text-sm text-gray-400 leading-relaxed">
            The plea is formatted dynamically. Forward this to the defense counsel for final signing.
          </p>

          <a
            href={downloadHref}
            target="_blank"
            rel="noreferrer"
            download
            className="group relative mt-8 inline-flex items-center justify-center gap-2 overflow-hidden rounded-xl bg-emerald-600/10 border border-emerald-500/50 px-8 py-3.5 text-sm font-medium text-emerald-300 transition-all hover:bg-emerald-600/20 hover:text-white shadow-[0_0_20px_rgba(16,185,129,0.15)] hover:shadow-[0_0_30px_rgba(16,185,129,0.3)]"
          >
            <div className="absolute inset-0 bg-gradient-to-r from-emerald-500/10 to-teal-500/10 opacity-0 group-hover:opacity-100 transition-opacity" />
            <Download className="h-5 w-5 relative z-10" />
            <span className="relative z-10 text-white">Download Output PDF</span>
          </a>
        </div>
      </Card>

      <Card>
        <div className="mb-5 flex items-center justify-between border-b border-white/5 pb-4">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-md bg-blue-500/10 border border-blue-500/20">
              <Building2 className="h-4 w-4 text-blue-400" />
            </div>
            <h3 className="text-sm font-medium text-white">
              eCourts Registry Sync
            </h3>
          </div>
          {cs?.source && (
            <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-indigo-300">
              node: {cs.source}
            </span>
          )}
        </div>

        {courtFound && cs ? (
          <dl className="grid gap-3 sm:grid-cols-2">
            <KV label="Case ID" value={cs.case_number} />
            <KV label="Bench" value={cs.court} />
            <KV
              label="Next Listing"
              value={cs.next_hearing_date}
              icon={Calendar}
              highlight
            />
            <KV label="Stage" value={cs.case_stage} />
            <KV label="Last Synced" value={cs.last_updated} />
            <KV label="Presiding" value={cs.judge} />
          </dl>
        ) : (
          <div className="rounded-lg bg-white/5 p-4 text-center">
            <p className="text-sm text-gray-400">
              No registry match found for provided identifier. The application can still be filed physically.
            </p>
          </div>
        )}
      </Card>

      <button
        type="button"
        onClick={onReset}
        className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-[#0f0f13] px-5 py-4 text-sm font-medium text-gray-300 transition-colors hover:bg-white/5 hover:text-white"
      >
        Initialize New Process
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Error step
// ---------------------------------------------------------------------------

function ErrorStep({
  message,
  onReset,
}: {
  message: string;
  onReset: () => void;
}) {
  return (
    <Card className="border-rose-500/20 bg-rose-500/5">
      <div className="flex flex-col items-center text-center py-6">
        <div className="rounded-full bg-rose-500/20 p-4 ring-1 ring-rose-500/40 text-rose-400 shadow-[0_0_20px_rgba(225,29,72,0.2)]">
          <AlertTriangle className="h-10 w-10" />
        </div>
        <h2 className="mt-5 text-lg font-semibold text-white">
          System Exception Encountered
        </h2>
        <p className="mt-2.5 max-w-sm text-sm text-rose-200/70 leading-relaxed font-mono bg-black/40 p-3 rounded-lg border border-rose-500/10 break-words">
          {message}
        </p>
        <button
          type="button"
          onClick={onReset}
          className="mt-8 rounded-xl border border-rose-500/50 bg-[#1f111a] px-8 py-3 text-sm font-medium text-rose-200 transition-all hover:bg-rose-500/20 hover:text-white shadow-[0_0_15px_rgba(225,29,72,0.1)]"
        >
          Reset Environment
        </button>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Footer
// ---------------------------------------------------------------------------

function Footer() {
  return (
    <footer className="border-t border-white/5 bg-black/80 py-6 text-center text-xs font-mono text-gray-500 mt-auto">
      <div className="max-w-3xl mx-auto px-4 flex flex-col sm:flex-row justify-between items-center gap-2">
        <span>Mukti Protocol v1.0</span>
        <span className="flex items-center gap-1.5"><Brain className="h-3 w-3" /> AI-Assisted • Human-Verified</span>
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

function Card({ children, className = "" }: { children: React.ReactNode, className?: string }) {
  return (
    <div className={`rounded-2xl border border-white/10 bg-[#0d0d12] p-6 shadow-xl transition-all duration-300 hover:border-purple-500/30 hover:shadow-[0_0_30px_-5px_rgba(168,85,247,0.1)] backdrop-blur-sm ${className}`}>
      {children}
    </div>
  );
}

function Field({
  label,
  value,
}: {
  label: string;
  value?: string | number | null;
}) {
  return (
    <div className="rounded-lg bg-white/5 border border-white/5 px-4 py-3 transition-colors hover:bg-white/10">
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-gray-500 mb-1">
        {label}
      </dt>
      <dd className="text-sm font-medium text-gray-200">
        {value ?? <span className="text-gray-600">—</span>}
      </dd>
    </div>
  );
}

function KV({
  label,
  value,
  icon: Icon,
  highlight,
}: {
  label: string;
  value?: string | null;
  icon?: React.ComponentType<{ className?: string }>;
  highlight?: boolean;
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 transition-colors hover:bg-white/5 ${highlight ? 'border-purple-500/30 bg-purple-500/5' : 'border-white/5 bg-transparent'}`}>
      <div className={`flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest ${highlight ? 'text-purple-400' : 'text-gray-500'}`}>
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </div>
      <div className={`mt-1.5 text-sm font-medium ${highlight ? 'text-white' : 'text-gray-300'}`}>
        {value || <span className="text-gray-600">—</span>}
      </div>
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3.5 text-sm text-rose-300 shadow-[0_0_15px_rgba(225,29,72,0.1)] backdrop-blur-sm">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
      <span className="leading-snug">{message}</span>
    </div>
  );
}
