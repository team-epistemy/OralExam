-- migration_016: stop duplicate class sessions from concurrent syllabus processing.
--
-- Runtime already serializes the create path with a per-course advisory lock in
-- process_syllabus; this adds the hard DB backstop and cleans up any exact
-- duplicates created before that fix landed.

-- 1) Collapse existing exact-duplicate sessions (same org/course/document/date),
--    keeping the earliest. material.session_id references class_session ON DELETE
--    SET NULL, so a dropped duplicate simply detaches its (freshly created,
--    usually empty) material links rather than cascading any deletes.
DELETE FROM class_session cs
USING (
    SELECT session_id,
           row_number() OVER (
               PARTITION BY org_id, course_id, session_document, session_date
               ORDER BY created_at, session_id
           ) AS rn
    FROM class_session
    WHERE session_document IS NOT NULL
) d
WHERE cs.session_id = d.session_id AND d.rn > 1;

-- 2) At most one session per (org, course, document, date). Partial: untitled
--    sessions (session_document IS NULL) are exempt, matching the app's rule
--    that only titled sessions are uniqueness-checked.
CREATE UNIQUE INDEX IF NOT EXISTS ux_class_session_course_doc_date
    ON class_session (org_id, course_id, session_document, session_date)
    WHERE session_document IS NOT NULL;
