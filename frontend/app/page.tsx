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
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 text-slate-800">
      <Header />

      <div className="mx-auto max-w-4xl px-4 pb-24 pt-8 sm:pt-12">
        <StepIndicator step={step} />

        <div className="mt-8">
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
    </main>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header() {
  return (
    <header className="border-b border-slate-200 bg-white/70 backdrop-blur">
      <div className="mx-auto flex max-w-4xl items-center gap-3 px-4 py-5">
        <div className="rounded-xl bg-blue-900 p-2 text-white">
          <Scale className="h-6 w-6" />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            Project Mukti
          </h1>
          <p className="text-sm text-slate-500">
            Compassionate AI for bail eligibility audits — built for India's
            undertrial families
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
    <ol className="flex items-center justify-between gap-2">
      {STEPS.map((s, i) => {
        const done = i < activeIdx || step === "success" && i <= activeIdx;
        const active = i === activeIdx;
        return (
          <li key={s.key} className="flex flex-1 items-center gap-3">
            <div
              className={[
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold transition",
                active
                  ? "border-blue-700 bg-blue-700 text-white"
                  : done
                  ? "border-blue-700 bg-blue-50 text-blue-700"
                  : "border-slate-300 bg-white text-slate-400",
              ].join(" ")}
            >
              {done ? <CheckCircle2 className="h-5 w-5" /> : i + 1}
            </div>
            <span
              className={[
                "hidden text-sm font-medium sm:block",
                active ? "text-blue-900" : done ? "text-blue-700" : "text-slate-400",
              ].join(" ")}
            >
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <div
                className={[
                  "ml-1 h-px flex-1 transition",
                  i < activeIdx ? "bg-blue-700" : "bg-slate-200",
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
    <section className="space-y-6">
      <Card>
        <h2 className="text-lg font-semibold text-slate-900">
          Upload the First Information Report
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          A clear photograph or scan of the FIR helps us read the case details
          accurately. Your document never leaves this session.
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
            "mt-5 flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-10 text-center transition",
            dragOver
              ? "border-blue-600 bg-blue-50"
              : file
              ? "border-emerald-400 bg-emerald-50/50"
              : "border-slate-300 bg-slate-50 hover:bg-slate-100",
          ].join(" ")}
        >
          <input
            ref={inputRef}
            type="file"
            accept="image/*,application/pdf"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <div className="flex items-center gap-3">
              <FileText className="h-10 w-10 text-emerald-600" />
              <div className="text-left">
                <p className="font-medium text-slate-900">{file.name}</p>
                <p className="text-xs text-slate-500">
                  {(file.size / 1024).toFixed(1)} KB · click to replace
                </p>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setFile(null);
                }}
                className="ml-3 rounded-full p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                aria-label="Remove file"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="mb-3 h-10 w-10 text-slate-400" />
              <p className="font-medium text-slate-700">
                Drag & drop the FIR image here
              </p>
              <p className="mt-1 text-sm text-slate-500">
                or <span className="text-blue-700 underline">browse files</span>
                {" "}— PNG, JPG, or PDF
              </p>
            </>
          )}
        </div>
      </Card>

      <Card>
        <label className="block">
          <span className="text-sm font-semibold text-slate-900">
            CNR Number{" "}
            <span className="font-normal text-slate-400">(optional)</span>
          </span>
          <p className="mt-1 text-sm text-slate-500">
            16-character court reference. Lets us pull the latest hearing date
            from eCourts.
          </p>
          <input
            type="text"
            value={cnr}
            onChange={(e) => setCnr(e.target.value.toUpperCase())}
            maxLength={16}
            placeholder="e.g. MHCC010012345-2024"
            className="mt-3 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm tracking-wider shadow-sm focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
          />
        </label>
      </Card>

      {error && <InlineError message={error} />}

      <button
        type="button"
        onClick={onSubmit}
        disabled={!file}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-blue-900 px-6 py-3.5 font-semibold text-white shadow-sm transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        <ShieldCheck className="h-5 w-5" />
        Start analysis
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Processing
// ---------------------------------------------------------------------------

const PROCESSING_STAGES = [
  { icon: FileSearch, label: "Scanning document…" },
  { icon: Brain, label: "Analyzing legal context…" },
  { icon: Gavel, label: "Calculating bail score…" },
];

function ProcessingStep() {
  const [active, setActive] = useState(0);
  useEffect(() => {
    const t = setInterval(
      () => setActive((i) => (i + 1) % PROCESSING_STAGES.length),
      2000
    );
    return () => clearInterval(t);
  }, []);
  return (
    <Card>
      <div className="flex flex-col items-center gap-6 py-6">
        <div className="relative">
          <Loader2 className="h-12 w-12 animate-spin text-blue-700" />
        </div>
        <h2 className="text-lg font-semibold text-slate-900">
          Working on your case…
        </h2>
        <ul className="w-full max-w-md space-y-2">
          {PROCESSING_STAGES.map((s, i) => {
            const Icon = s.icon;
            const isActive = i === active;
            const isDone = i < active;
            return (
              <li
                key={s.label}
                className={[
                  "flex items-center gap-3 rounded-lg border px-4 py-3 transition",
                  isActive
                    ? "border-blue-200 bg-blue-50 text-blue-900"
                    : isDone
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-slate-200 bg-white text-slate-400",
                ].join(" ")}
              >
                {isDone ? (
                  <CheckCircle2 className="h-5 w-5" />
                ) : isActive ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  <Icon className="h-5 w-5" />
                )}
                <span className="text-sm font-medium">{s.label}</span>
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-slate-400">
          This usually takes 10–30 seconds.
        </p>
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
      ? "bg-emerald-600"
      : score >= 40
      ? "bg-amber-500"
      : "bg-rose-500";
  const scoreLabel =
    score >= 70 ? "Strong grounds" : score >= 40 ? "Some grounds" : "Weak grounds";

  return (
    <section className="space-y-6">
      <Card>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">
              Advocate review
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              The AI has prepared a draft. A licensed advocate must review
              before any document is filed in court.
            </p>
          </div>
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">
            <AlertTriangle className="h-3.5 w-3.5" />
            Awaiting review
          </span>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          <Field label="Accused" value={preview.accused_name} />
          <Field label="FIR Number" value={preview.fir_number} />
          <Field
            label="Days in Custody"
            value={
              preview.days_in_custody !== undefined
                ? `${preview.days_in_custody} days`
                : null
            }
          />
          <Field
            label="Applicable Sections"
            value={
              preview.applicable_sections?.length
                ? preview.applicable_sections.join(", ")
                : null
            }
          />
        </div>

        <div className="mt-6">
          <div className="mb-1.5 flex items-center justify-between text-sm">
            <span className="font-semibold text-slate-700">
              Confidence score
            </span>
            <span className="text-slate-500">
              {scoreLabel} · {score}/100
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full rounded-full transition-all ${scoreColor}`}
              style={{ width: `${score}%` }}
            />
          </div>
        </div>
      </Card>

      <Card>
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Plain-language summary
        </h3>
        <p className="mt-3 whitespace-pre-line text-sm leading-6 text-slate-700">
          {preview.summary_excerpt || "No summary available yet."}
        </p>
      </Card>

      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
        <div className="flex gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 text-amber-700" />
          <p className="text-sm text-amber-900">
            <strong>This requires review by a licensed advocate.</strong>{" "}
            Project Mukti is an AI assistant — it identifies provisions that
            <em> may </em> apply. Do not file this document without legal
            counsel.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-5 py-3 font-semibold text-slate-700 hover:bg-slate-50"
        >
          <ArrowLeft className="h-4 w-4" />
          Cancel & start over
        </button>
        <button
          type="button"
          onClick={onApprove}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-blue-900 px-5 py-3 font-semibold text-white shadow-sm hover:bg-blue-800"
        >
          <CheckCircle2 className="h-5 w-5" />
          Approve & generate PDF
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
    <section className="space-y-6">
      <Card>
        <div className="flex flex-col items-center text-center">
          <div className="rounded-full bg-emerald-100 p-3">
            <CheckCircle2 className="h-10 w-10 text-emerald-600" />
          </div>
          <h2 className="mt-4 text-xl font-semibold text-slate-900">
            Your bail application is ready
          </h2>
          <p className="mt-1 max-w-md text-sm text-slate-500">
            Download the PDF and take it to a Zila Vidhi Seva Pradhikaran
            (free legal aid clinic) for an advocate to review and file.
          </p>

          <a
            href={downloadHref}
            target="_blank"
            rel="noreferrer"
            download
            className="mt-6 inline-flex items-center justify-center gap-2 rounded-xl bg-blue-900 px-6 py-3.5 font-semibold text-white shadow-sm hover:bg-blue-800"
          >
            <Download className="h-5 w-5" />
            Download bail application PDF
          </a>
        </div>
      </Card>

      <Card>
        <div className="mb-4 flex items-center gap-2">
          <Building2 className="h-5 w-5 text-blue-700" />
          <h3 className="text-base font-semibold text-slate-900">
            Live court status
          </h3>
          {cs?.source && (
            <span className="ml-auto rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
              source: {cs.source}
            </span>
          )}
        </div>

        {courtFound && cs ? (
          <dl className="grid gap-3 sm:grid-cols-2">
            <KV label="Case Number" value={cs.case_number} />
            <KV label="Court" value={cs.court} />
            <KV
              label="Next hearing"
              value={cs.next_hearing_date}
              icon={Calendar}
            />
            <KV label="Stage" value={cs.case_stage} />
            <KV label="Last updated" value={cs.last_updated} />
            <KV label="Judge" value={cs.judge} />
          </dl>
        ) : (
          <p className="text-sm text-slate-500">
            No court status was retrieved. You can still file the application —
            the registry will assign a CNR after acceptance.
          </p>
        )}
      </Card>

      <button
        type="button"
        onClick={onReset}
        className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-5 py-3 font-semibold text-slate-700 hover:bg-slate-50"
      >
        Start a new case
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
    <Card>
      <div className="flex flex-col items-center text-center">
        <div className="rounded-full bg-rose-100 p-3">
          <AlertTriangle className="h-10 w-10 text-rose-600" />
        </div>
        <h2 className="mt-4 text-lg font-semibold text-slate-900">
          We couldn't complete your request
        </h2>
        <p className="mt-2 max-w-md text-sm text-slate-600">{message}</p>
        <p className="mt-2 text-xs text-slate-400">
          If the backend is offline, ensure <code>uvicorn main:app</code> is
          running on port 8000.
        </p>
        <button
          type="button"
          onClick={onReset}
          className="mt-6 rounded-xl bg-blue-900 px-5 py-3 font-semibold text-white hover:bg-blue-800"
        >
          Try again
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
    <footer className="border-t border-slate-200 bg-white/60 py-6 text-center text-xs text-slate-500">
      Project Mukti — AI-assisted, human-reviewed. Not a substitute for legal
      counsel.
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
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
    <div className="rounded-lg bg-slate-50 px-4 py-3">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 text-sm font-semibold text-slate-900">
        {value ?? "—"}
      </dd>
    </div>
  );
}

function KV({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value?: string | null;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
        {Icon && <Icon className="h-3.5 w-3.5" />}
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold text-slate-900">
        {value || "—"}
      </div>
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}
