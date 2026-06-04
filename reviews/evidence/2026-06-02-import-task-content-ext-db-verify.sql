-- Executed after live apply on 2026-06-02.
SELECT
    external_uid,
    task_content->'stem_images' AS stem_images,
    task_content->'hints_video' AS hints_video
FROM tasks
WHERE external_uid IN ('TEST-SC-001', 'TEST-SC-002', 'TEST-SC-003')
ORDER BY external_uid;

-- Observed:
-- TEST-SC-001 | null          | []
-- TEST-SC-002 | ["graph.png"] | ["url"]
-- TEST-SC-003 | null          | null
