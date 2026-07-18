-- 0004_model_artifacts: named jsonb outputs attached to a model run that are
-- not per-entity rows — learned regression weights, sensitivity sweeps,
-- anything /methodology renders directly. Same lifecycle as every other
-- output table: replaced wholesale when the run is replaced.

CREATE TABLE model_artifacts (
  run_id  int NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  name    text NOT NULL,
  payload jsonb NOT NULL,
  PRIMARY KEY (run_id, name)
);
