import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "@/db/schema";

const url =
  process.env.DATABASE_URL ?? "postgres://cdlhub:cdlhub@localhost:54329/cdlhub";

// Neon and other poolers prefer few connections from serverless functions.
const client = postgres(url, { max: 5 });

export const db = drizzle(client, { schema });
