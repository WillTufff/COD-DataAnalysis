import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "@/db/schema";

const url =
  process.env.DATABASE_URL ?? "postgres://cdlhub:cdlhub@localhost:54329/cdlhub";

// Prerendering runs several build workers in parallel and each one instantiates
// this module, so a runtime-sized pool per worker multiplies out and exhausts
// the server's connection slots ("too many clients already"). A worker renders
// its pages one at a time, so one connection each is enough; the pool only
// needs room to spare at request time. Neon and other poolers likewise prefer
// few connections per serverless function.
const building = process.env.NEXT_PHASE === "phase-production-build";

const client = postgres(url, {
  max: building ? 1 : 5,
  // Don't hold a slot open between the bursts of queries a page render makes.
  idle_timeout: building ? 5 : 30,
});

export const db = drizzle(client, { schema });
