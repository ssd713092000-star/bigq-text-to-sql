import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: { id: string; orgSlug: string } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    apiToken?: string; // server-side only; never copied into Session
    apiTokenExpires?: number;
    userId?: string;
    orgSlug?: string;
  }
}
