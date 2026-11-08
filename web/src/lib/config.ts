// Server-side settings. The browser never calls the API directly (see app/api/backend).
export const API_URL = (process.env.API_URL ?? "http://localhost:8080").replace(/\/$/, "");

// Auth.js uses the "__Secure-" cookie prefix on https; getToken() must look for the same name.
export const SECURE_COOKIES = (process.env.AUTH_URL ?? "").startsWith("https://");
