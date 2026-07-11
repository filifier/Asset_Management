/*
 * auth.js — Supabase login + cloud portfolio storage.
 *
 * Why Supabase: the site is static (GitHub Pages, no server of our own),
 * so to have real accounts + cross-device portfolio storage we use a
 * Backend-as-a-Service the browser talks to directly. Supabase handles
 * auth (email/password) and a Postgres table, with Row-Level Security so
 * each user can only read/write their OWN portfolio row.
 *
 * The two values below are PUBLIC by design:
 *   - SUPABASE_URL: the project URL
 *   - SUPABASE_ANON_KEY: the "anon" (public) key — safe to ship in
 *     client JS. It grants nothing on its own; RLS policies (defined in
 *     the DB, see README "Login e cloud") enforce that a logged-in user
 *     only touches their own row. This is NOT a secret key.
 *
 * Fill these in after creating the Supabase project. Until they're set,
 * authConfigured() returns false and the app falls back to localStorage
 * (so the site keeps working before login is wired up).
 */

const SUPABASE_URL = "https://dqezcapwpvmjsczuvyfi.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRxZXpjYXB3cHZtanNjenV2eWZpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODM3MzQ3MTgsImV4cCI6MjA5OTMxMDcxOH0.PJOziHw3t0EdiiLONhquvhEw-GNmhnECJBM-lGFRuM4";

let _sbClient = null;

function authConfigured() {
  return typeof SUPABASE_URL === "string" && SUPABASE_URL.startsWith("http");
}

function sbClient() {
  if (!_sbClient && authConfigured() && window.supabase) {
    _sbClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  }
  return _sbClient;
}

async function authGetUser() {
  const c = sbClient();
  if (!c) return null;
  const { data } = await c.auth.getUser();
  return data ? data.user : null;
}

async function authSignUp(email, password) {
  const c = sbClient();
  if (!c) return { error: { message: "Login non configurato." } };
  const { data, error } = await c.auth.signUp({ email, password });
  // With email confirmation ON, signUp returns a user but NO session
  // (session is null until the user confirms) — the caller checks
  // `session` to know whether they're actually signed in.
  return { user: data && data.user, session: data && data.session, error };
}

async function authSignIn(email, password) {
  const c = sbClient();
  if (!c) return { error: { message: "Login non configurato." } };
  const { data, error } = await c.auth.signInWithPassword({ email, password });
  return { user: data && data.user, error };
}

async function authSignOut() {
  const c = sbClient();
  if (c) await c.auth.signOut();
}

// Fires cb(user|null) on sign-in / sign-out, so the UI can react.
function authOnChange(cb) {
  const c = sbClient();
  if (!c) return;
  c.auth.onAuthStateChange((_event, session) => cb(session ? session.user : null));
}

// ── cloud portfolio storage (one JSONB row per user) ──
async function cloudLoadPortfolio() {
  const c = sbClient();
  const user = await authGetUser();
  if (!c || !user) return null;
  const { data, error } = await c
    .from("portfolios")
    .select("data")
    .eq("user_id", user.id)
    .maybeSingle();
  if (error) {
    console.warn("cloudLoadPortfolio:", error.message);
    return null;
  }
  return data ? data.data : null;
}

async function cloudSavePortfolio(portfolio) {
  const c = sbClient();
  const user = await authGetUser();
  if (!c || !user) return;
  const { error } = await c.from("portfolios").upsert({
    user_id: user.id,
    data: portfolio,
    updated_at: new Date().toISOString(),
  });
  if (error) console.warn("cloudSavePortfolio:", error.message);
}
