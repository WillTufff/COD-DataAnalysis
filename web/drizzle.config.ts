// Migrations are hand-written SQL in ../db/migrations (source of truth).
// This config exists so `drizzle-kit` tooling (introspect/diff) can be used
// to verify db/schema.ts stays in sync with the live database.
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./db/schema.ts",
  out: "./drizzle", // scratch output only; never applied
  dbCredentials: {
    url:
      process.env.DATABASE_URL ??
      "postgres://cdlhub:cdlhub@localhost:54329/cdlhub",
  },
});
