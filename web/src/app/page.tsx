// Phase 1 placeholder. Sign-in arrives in phase 2, chat in phases 5-6.
export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-4 px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">Text-to-SQL Analyst</h1>
      <p className="text-zinc-600 dark:text-zinc-400">
        Ask questions about your BigQuery data in plain English. Sign in with Google is coming next.
      </p>
    </main>
  );
}
