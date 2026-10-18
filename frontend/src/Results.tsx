import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./lib";

type Result = {
  id: string; candidate_name: string | null; candidate_email: string;
  job_title: string; interview_name: string; attempt_number: number; status: string;
  started_at: string; completed_at: string | null; answered_count: number; question_count: number; average_score: number | null; evaluated_at: string | null;
};
type Detail = Result & { answers: { question_id: string; question: string; transcript: string | null; submitted_at: string | null; score: number | null; feedback: string | null }[] };

export default function Results() {
  const queryClient = useQueryClient();
  const evaluation = useMutation({ mutationFn: (id: string) => api<Detail>(`/api/v1/results/${id}/evaluate`, { method: "POST" }), onSuccess: (data, id) => { queryClient.setQueryData(["result", id], data); void queryClient.invalidateQueries({ queryKey: ["results"] }); } });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const results = useQuery({ queryKey: ["results", offset], queryFn: () => api<Result[]>(`/api/v1/results?offset=${offset}&limit=20`) });
  const detail = useQuery({ queryKey: ["result", selected], queryFn: () => api<Detail>(`/api/v1/results/${selected}`), enabled: Boolean(selected) });
  return <section id="results" className="card mt-6" aria-labelledby="results-heading">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div><h2 id="results-heading" className="text-xl font-semibold">5. Interview results</h2><p className="mt-2 text-sm text-slate-400">Review answers and evaluate each question out of 10. AI assessments support your review; they do not make hiring decisions.</p></div>
      <button className="button" type="button" disabled={results.isFetching || detail.isFetching} onClick={() => { void results.refetch(); if (selected) void detail.refetch(); }}>Refresh results</button>
    </div>
    {results.isPending && <p className="mt-5" role="status">Loading results…</p>}
    {results.error && <p className="mt-5 text-rose-400" role="alert">{results.error.message}</p>}
    {results.isSuccess && !results.data.length && <p className="mt-5 text-slate-400">No interview attempts on this page. Results appear after a candidate starts an accepted interview.</p>}
    <div className="mt-5 grid gap-3">{results.data?.map(result => <article key={result.id} className="rounded-xl border border-slate-700 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0"><h3 className="font-semibold">{result.candidate_name || result.candidate_email}</h3><p className="break-words text-sm text-slate-400">{result.candidate_email}</p><p className="mt-2">{result.interview_name} · {result.job_title}</p><p className="mt-1 text-sm text-cyan-300">Attempt {result.attempt_number} · {result.status === "active" ? "In progress" : result.status} · {result.answered_count}/{result.question_count} answers</p>{result.average_score !== null && <p className="mt-2 font-semibold text-emerald-300">Average score: {result.average_score}/10</p>}<p className="mt-1 text-xs text-slate-400">Started {new Date(result.started_at).toLocaleString()}{result.completed_at && ` · Completed ${new Date(result.completed_at).toLocaleString()}`}</p></div>
        <button className="button" type="button" aria-expanded={selected === result.id} onClick={() => setSelected(selected === result.id ? null : result.id)}>{selected === result.id ? "Hide answers" : "View answers"}</button>
      </div>
      {selected === result.id && <div className="mt-4 border-t border-slate-700 pt-4">
        {detail.data && <div className="mb-5"><button className="button" type="button" disabled={evaluation.isPending || detail.data.evaluated_at !== null || !["completed", "expired"].includes(detail.data.status)} onClick={() => evaluation.mutate(result.id)}>{evaluation.isPending && evaluation.variables === result.id ? "Evaluating answers…" : detail.data.evaluated_at ? "Answers evaluated" : "Evaluate answers out of 10"}</button>{!["completed", "expired"].includes(detail.data.status) && <p className="mt-2 text-sm text-slate-400">Evaluation becomes available after completion or expiry.</p>}{detail.data.average_score !== null && <p className="mt-3 font-semibold text-emerald-300">Overall average: {detail.data.average_score}/10</p>}{evaluation.error && evaluation.variables === result.id && <p role="alert" className="mt-3 text-rose-400">{evaluation.error.message}</p>}</div>}
        {detail.isPending && <p role="status">Loading answers…</p>}
        {detail.error && <p role="alert" className="text-rose-400">{detail.error.message}</p>}
        {detail.data?.answers.map((answer, index) => <div key={answer.question_id} className="mb-5 last:mb-0"><h4 className="font-medium">{index + 1}. {answer.question}</h4><p className="mt-2 whitespace-pre-wrap break-words text-slate-300">{answer.transcript ?? "No answer submitted."}</p>{answer.score !== null && <div className="mt-3 rounded-lg bg-slate-950 p-3"><p className="font-semibold text-emerald-300">AI score: {answer.score}/10</p><p className="mt-1 whitespace-pre-wrap text-sm text-slate-300">{answer.feedback}</p></div>}{answer.submitted_at && <p className="mt-2 text-xs text-slate-500">Saved {new Date(answer.submitted_at).toLocaleString()}</p>}</div>)}
      </div>}
    </article>)}</div>
    <nav className="mt-5 flex gap-3" aria-label="Results pagination"><button className="button" type="button" disabled={offset === 0 || results.isFetching} onClick={() => { setSelected(null); setOffset(value => Math.max(0, value - 20)); }}>Previous</button><button className="button" type="button" disabled={results.isFetching || !results.data || results.data.length < 20} onClick={() => { setSelected(null); setOffset(value => value + 20); }}>Next</button></nav>
  </section>;
}
