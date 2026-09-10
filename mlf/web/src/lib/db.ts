import { Pool } from "pg";

let pool: Pool | null = null;

export function getPool(): Pool {
  const dsn =
    process.env.MLF_POSTGRES_DSN ??
    process.env.POSTGRES_DSN;

  if (!dsn) {
    throw new Error(
      "MLF PostgreSQL DSN is not configured.",
    );
  }

  if (!pool) {
    pool = new Pool({
      connectionString: dsn,
      max: 5,
      idleTimeoutMillis: 30_000,
      allowExitOnIdle: true,
      application_name: "mlf-nextjs",
    });
  }

  return pool;
}