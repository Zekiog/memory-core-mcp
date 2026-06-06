-- Run as ADMIN on zmemory-adb (Oracle 23ai).
-- Creates a least-privilege application schema ZMEM and grants needed for
-- relational + JSON + native AI Vector Search + in-DB ONNX embedding.

-- 1) Application user (replace &zmem_pw at runtime; do not hardcode).
CREATE USER ZMEM IDENTIFIED BY "&zmem_pw"
  DEFAULT TABLESPACE DATA
  QUOTA UNLIMITED ON DATA;

GRANT CREATE SESSION TO ZMEM;
GRANT CREATE TABLE        TO ZMEM;
GRANT CREATE VIEW         TO ZMEM;
GRANT CREATE SEQUENCE     TO ZMEM;
GRANT CREATE PROCEDURE    TO ZMEM;
GRANT CREATE MINING MODEL TO ZMEM;          -- required to load ONNX embedding model
GRANT EXECUTE ON DBMS_VECTOR        TO ZMEM;
GRANT EXECUTE ON DBMS_VECTOR_CHAIN  TO ZMEM;
GRANT EXECUTE ON DBMS_CLOUD         TO ZMEM; -- to stage the ONNX model file

-- 2) (Optional) DB resource guardrails for the free tier.
ALTER USER ZMEM PROFILE DEFAULT;
