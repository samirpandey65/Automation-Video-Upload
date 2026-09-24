# Scheduler Retry Handling

This build makes scheduled uploads durable and retryable.

## Behavior

- Gemini/AI timeout does not permanently fail the video.
- A durable `upload_jobs` row is created before AI analysis.
- AI failures leave the video `pending` and the job `retry_pending`.
- Retry attempts use exponential backoff: 1, 2, 4, 8, 16 minutes.
- Up to 5 durable attempts are allowed.
- Every retry checks the YouTube SHA marker before uploading, preventing duplicates.
- Missing source files are marked failed immediately.
- YouTube resumable upload chunk errors are retried up to 5 times.
- Optional dashboard Gemini quote failures are non-fatal and logged at DEBUG level.
- A schedule is not marked as successfully run when its video is waiting for retry.

## Important

Do not delete `data/uploader.db`. The retry queue is persisted in SQLite.
