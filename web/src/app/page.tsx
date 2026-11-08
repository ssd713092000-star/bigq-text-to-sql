import { Suspense } from "react";

import { auth, signIn, signOut } from "@/auth";
import { ApiError, apiFetch, type Me } from "@/lib/api";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 px-4 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Text-to-SQL Analyst</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Ask questions about your BigQuery data in plain English. Queries run with your own Google
          account, so you only see data you already have access to.
        </p>
      </header>
      <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>
        <Account />
      </Suspense>
    </main>
  );
}

async function Account() {
  const session = await auth();
  if (!session) return <SignInButton />;

  let me: Me;
  try {
    me = await apiFetch<Me>("/me?check_google=true");
  } catch (e) {
    // API session gone (expired / revoked / DB reset): start over
    if (e instanceof ApiError && e.status === 401) return <SignInButton note="Your session ended. Sign in again." />;
    throw e;
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
      <div>
        <p className="font-medium">{me.name ?? me.email}</p>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">{me.email}</p>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Org: {me.org.display_name} {me.org.is_personal ? "(personal)" : `(${me.org.slug})`}
        </p>
      </div>
      <ul className="text-sm">
        <Check ok={me.google_connected} label="Google account connected" />
        <Check ok={me.bigquery_scope_granted} label="BigQuery permission granted" />
        <Check ok={me.google_token_ok === true} label="Google access token works" />
      </ul>
      {!me.bigquery_scope_granted && (
        <p className="text-sm text-amber-700 dark:text-amber-400">
          BigQuery access wasn&apos;t granted. Sign out and sign in again, and keep the BigQuery box ticked on
          Google&apos;s consent screen.
        </p>
      )}
      <form
        action={async () => {
          "use server";
          await signOut({ redirectTo: "/" });
        }}
      >
        <button className="rounded-md border border-zinc-300 px-4 py-2 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900">
          Sign out
        </button>
      </form>
    </section>
  );
}

function SignInButton({ note }: { note?: string }) {
  return (
    <form
      className="flex flex-col items-start gap-2"
      action={async () => {
        "use server";
        await signIn("google", { redirectTo: "/" });
      }}
    >
      {note && <p className="text-sm text-zinc-600 dark:text-zinc-400">{note}</p>}
      <button className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300">
        Sign in with Google
      </button>
    </form>
  );
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={ok ? "text-emerald-700 dark:text-emerald-400" : "text-zinc-500"}>
      {ok ? "✓" : "✗"} {label}
    </li>
  );
}
