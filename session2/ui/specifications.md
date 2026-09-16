# Lecture-to-Transcript Application Specification

**Version:** 1.0 draft

**Purpose:** A shared design for the frontend and backend teams to implement.

The application lets a user upload a lecture video or audio file, generate a transcript, preview it, and download it as a text file. The frontend team builds the website in `ui/`. The backend team implements media processing and the API in `source-to-transcript/`, following the project split described in `session2/Overview.md`.

The paths, field names, and response formats below are the proposed agreement between the teams. File formats and size limits are initial product decisions that the teams can revise together before implementation. This document specifies behavior; it does not mean that the UI or backend already exists.

## 1. UI design

Use a simple, responsive single-page layout that works on desktop and mobile. Present the workflow from top to bottom: **Upload → Transcribe → Download**.

| Major element | Content and behavior |
| --- | --- |
| Header | Application name, “Lecture to Transcript,” and a short explanation of what it does. |
| Upload area | A “Choose file” button and a drag-and-drop area. Accept one audio or video file at a time. Show supported formats and the size limit. |
| Selected file | Display the filename and size, with controls to remove or replace it before submission. |
| Main action | A “Generate transcript” button. Enable it only when a valid file is selected. Disable it while uploading or processing to prevent duplicate submissions. |
| Status area | Show “Uploading,” “Queued,” “Transcribing,” “Completed,” or a useful error message. Use a loading indicator while waiting. |
| Transcript preview | Display the completed transcript as readable, selectable plain text. Preserve paragraphs and line breaks. Keep this area read-only in version 1. |
| Download and restart | Enable “Download transcript (.txt)” only after completion. Provide “Transcribe another file” after completion or failure. |

### UI behavior

- Validate the file type, nonzero size, and size limit before uploading. The backend must also validate the upload.
- Make file selection and all buttons usable by keyboard. Provide visible labels, a visible focus indicator, and screen-reader announcements for status changes.
- Show an indeterminate indicator during transcription; the version 1 API does not provide a completion percentage or time estimate.
- Display errors next to the relevant control and explain how to recover, such as choosing another file or retrying a status check.
- Render filenames, transcript text, and error messages as text, never as HTML.
- If a status check loses its network connection, keep the job ID so the user can retry checking the same job without uploading again.
- Starting another transcription clears the previous file, transcript, and job state in the UI. It does not delete backend data.

Version 1 includes one file per job and `.txt` downloads. Accounts, saved transcript history, transcript editing, subtitle files, speaker identification, and job cancellation are outside this version's scope.

## 2. API overview

An API endpoint is a URL the frontend calls to request an action from the backend. The HTTP method indicates the action: `POST` submits new work, and `GET` retrieves information or a file.

The API uses the base path `/api/v1`. During local development, the backend can run at `http://localhost:8000`, making the full upload URL `http://localhost:8000/api/v1/transcriptions`. The frontend must keep the backend address configurable.

| Method | Endpoint | Purpose | Success response |
| --- | --- | --- | --- |
| `POST` | `/api/v1/transcriptions` | Upload one file and create a transcription job. | `202 Accepted` with a JSON job object. |
| `GET` | `/api/v1/transcriptions/{job_id}` | Check job status and obtain the transcript when ready. | `200 OK` with a JSON job object. |
| `GET` | `/api/v1/transcriptions/{job_id}/transcript` | Download the completed transcript. | `200 OK` with a UTF-8 text file. |

`{job_id}` means “replace this part of the URL with the ID returned by the upload request.”

### Why use a job?

A lecture can take time to transcribe. After accepting the upload, the backend returns a job ID without waiting for transcription to finish. The frontend then checks that job periodically. `202 Accepted` means that work was accepted; it does not mean that the transcript is ready. See the [HTTP 202 reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/202).

The complete flow is:

1. The user selects a file and clicks “Generate transcript.”
2. The frontend uploads the file with `POST /api/v1/transcriptions`.
3. The backend validates and stores the upload, accepts the job, and returns its ID.
4. The backend transcribes the file in the background.
5. The frontend checks `GET /api/v1/transcriptions/{job_id}` every 2 seconds, waiting for each response before scheduling the next request.
6. When the status becomes `completed`, the frontend stops checking, displays the transcript, and enables download. If it becomes `failed`, the frontend stops checking and displays the error.
7. Clicking download requests `GET /api/v1/transcriptions/{job_id}/transcript`.

## 3. Upload request

**Endpoint:** `POST /api/v1/transcriptions`

**Request format:** `multipart/form-data`. This is the standard browser form format for sending a file. The file is sent as binary data, not inside JSON and not encoded as Base64.

| Form field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `file` | Binary file | Yes | The lecture audio or video to transcribe. |

Only one `file` field is allowed. There are no other upload fields in version 1.

### File requirements

- Supported filename extensions, checked without regard to letter case: `.mp3`, `.wav`, `.m4a`, `.mp4`, and `.webm`.
- Maximum file size: **500 MiB = 524,288,000 bytes**, excluding the multipart form overhead. A file exactly at the limit is allowed.
- Empty files are rejected.
- The file must contain a decodable audio stream. For video, the backend extracts and transcribes the audio.
- The backend validates actual media content; a filename or browser-provided MIME type alone does not prove the file is supported. Container extensions do not guarantee that a codec is decodable.

Example frontend request:

```javascript
// selectedFile is the File chosen using the upload control.
const formData = new FormData();
formData.append("file", selectedFile);

const response = await fetch(`${API_BASE_URL}/api/v1/transcriptions`, {
  method: "POST",
  body: formData,
});

const body = await response.json();
if (!response.ok) {
  throw new Error(body.error.message);
}

const jobId = body.id;
```

`API_BASE_URL` is the configurable server address, such as `http://localhost:8000`, without a trailing slash. It can be an empty string when the UI and API share the same origin. Do not set the `Content-Type` header manually for this request: the browser adds the multipart boundary. See [Using FormData](https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequest_API/Using_FormData_Objects).

### Successful upload response

Return `202 Accepted` with `Content-Type: application/json`:

```json
{
  "id": "9e83ad26-5d48-4c6a-bd92-a9a4f8ec0241",
  "filename": "lecture-01.mp4",
  "status": "queued",
  "transcript": null,
  "error": null
}
```

The backend must create a retrievable job before sending this response. The upload response always reports `queued`; a subsequent status request may already report a later state.

## 4. Job status and transcript data

**Endpoint:** `GET /api/v1/transcriptions/{job_id}`

No request body is required. Return `200 OK` and `Content-Type: application/json` for an existing job, including a job whose transcription failed. Use `Cache-Control: no-store` for job responses so the UI receives the current status.

### Job object

All five fields are required in every job response. Use JSON `null` for values that are not available; do not omit those fields or send the string `"null"`.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | String | A unique backend-generated UUID identifying this job. The frontend treats it as an opaque value. |
| `filename` | String | The original filename for display, with directory components removed. |
| `status` | String | Exactly one of `queued`, `processing`, `completed`, or `failed`. |
| `transcript` | Object or `null` | `{"text": "..."}` only when completed; otherwise `null`. |
| `error` | Object or `null` | `{"code": "...", "message": "..."}` only when failed; otherwise `null`. |

### Status rules

| Status | Meaning | `transcript` | `error` |
| --- | --- | --- | --- |
| `queued` | Upload accepted, waiting for processing. | `null` | `null` |
| `processing` | Audio extraction or transcription is running. | `null` | `null` |
| `completed` | Transcript is ready to preview and download. | Object with nonempty `text` | `null` |
| `failed` | Processing ended unsuccessfully. | `null` | Error object |

Normal transitions are `queued → processing → completed` or `queued → processing → failed`. A job may also move directly from `queued` to `failed` if it cannot start. `completed` and `failed` are final states. A polling client may miss intermediate states. Retrying a failed transcription creates a new job with a new upload.

### Completed response example

```json
{
  "id": "9e83ad26-5d48-4c6a-bd92-a9a4f8ec0241",
  "filename": "lecture-01.mp4",
  "status": "completed",
  "transcript": {
    "text": "Welcome to today's lecture.\n\nWe will begin with an introduction to machine learning."
  },
  "error": null
}
```

`transcript.text` is Unicode plain text. JSON represents line breaks as `\n`; after parsing JSON, those become actual line breaks. The transcript contains the recognized speech in its original language, without translation, timestamps, or speaker labels required in version 1.

### Failed response example

```json
{
  "id": "9e83ad26-5d48-4c6a-bd92-a9a4f8ec0241",
  "filename": "lecture-01.mp4",
  "status": "failed",
  "transcript": null,
  "error": {
    "code": "INVALID_MEDIA",
    "message": "The uploaded video does not contain a readable audio stream."
  }
}
```

Processing error codes are `UNSUPPORTED_MEDIA_TYPE`, `INVALID_MEDIA`, `NO_SPEECH`, and `TRANSCRIPTION_FAILED`. Use `NO_SPEECH` when processing succeeds technically but no speech is recognized, instead of returning an empty completed transcript. Unexpected processing errors or worker timeouts must transition the job to `failed` with `TRANSCRIPTION_FAILED`.

## 5. Transcript download

**Endpoint:** `GET /api/v1/transcriptions/{job_id}/transcript`

No request body is required. For a completed job, return `200 OK` with:

```http
Content-Type: text/plain; charset=utf-8
Content-Disposition: attachment; filename="transcript-9e83ad26-5d48-4c6a-bd92-a9a4f8ec0241.txt"
Cache-Control: no-store
```

Use `transcript-{job_id}.txt` as the download filename. The response body is the exact value of `transcript.text` encoded as UTF-8, without a JSON wrapper or extra quotation marks. JSON-escaped line breaks become actual line breaks in the file.

Return a JSON error with `409 Conflict` if the job is still queued or processing (`TRANSCRIPT_NOT_READY`), or if it failed (`TRANSCRIPTION_FAILED`). Return `404 Not Found` if the job does not exist. The frontend must check whether the download succeeded before saving the response, so an error response is not saved as a transcript.

## 6. API errors

When an API request itself fails, return an appropriate non-success HTTP status and `Content-Type: application/json`, using this format:

```json
{
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "The file exceeds the 500 MiB upload limit."
  }
}
```

Both error fields are required strings. `code` is a stable identifier the UI can use in logic. `message` is a user-readable explanation whose wording may vary. Do not return stack traces or internal file paths to the UI.

| HTTP status | Error code | When to use it |
| --- | --- | --- |
| `400 Bad Request` | `INVALID_REQUEST` | Missing or empty file, multiple files, unexpected form fields, or a malformed upload request. |
| `413 Content Too Large` | `FILE_TOO_LARGE` | File exceeds the size limit. |
| `415 Unsupported Media Type` | `UNSUPPORTED_MEDIA_TYPE` | Upload is not multipart form data, or the media format is unsupported. |
| `422 Unprocessable Content` | `INVALID_MEDIA` | Corrupt media or missing audio detected before the job is accepted. |
| `404 Not Found` | `JOB_NOT_FOUND` | Malformed, unknown, or no-longer-available job ID on a status or download request. |
| `409 Conflict` | `TRANSCRIPT_NOT_READY` | Download requested while queued or processing. |
| `409 Conflict` | `TRANSCRIPTION_FAILED` | Download requested for a failed job; check the job status for the underlying error. |
| `500 Internal Server Error` | `INTERNAL_ERROR` | An unexpected error prevents the API from handling the request. |
| `503 Service Unavailable` | `SERVICE_UNAVAILABLE` | Backend temporarily cannot accept or handle the request. |

If a media problem is discovered after the backend has returned `202`, report it through the job's `failed` status. A successful status lookup still returns HTTP `200`; the `status` field describes the outcome of the transcription.

Network failures and infrastructure errors may have no JSON response. The UI must handle these with a general connection/error message. Do not automatically repeat an upload after a lost response, because the original request may already have created a job. Status and download requests can be retried without creating a new job.

## 7. Frontend/backend coordination

- **Frontend responsibilities:** file selection, client-side validation, upload, status polling, transcript display, download controls, and clear loading/error states.
- **Backend responsibilities:** server-side validation, job creation and tracking, background transcription, status responses, and transcript downloads. Backend implementation language and transcription engine are independent of this contract.
- **Local development:** if the UI and backend use different origins, the backend must allow the configured UI origin through CORS and support the `POST`, `GET`, and `OPTIONS` methods. No authentication or credentials are included in this local prototype.
- **Storage:** the local prototype may keep job metadata and transcripts in memory. A server restart may make earlier job IDs unavailable; the UI should handle `JOB_NOT_FOUND` by offering a new upload. Delete temporary uploaded media after processing finishes or fails. Persistent storage and retention rules can be designed in a later version.
- **API stability:** coordinate changes to endpoint paths, required fields, status values, limits, and error codes with both teams. The frontend should tolerate additional response fields it does not use.

## 8. Acceptance checklist

- [ ] A supported audio file can be uploaded, transcribed, previewed, and downloaded.
- [ ] A supported video file follows the same workflow using its audio stream.
- [ ] The UI handles queued, processing, completed, and failed jobs correctly and stops polling on a final state.
- [ ] Missing, empty, unsupported, corrupt, and oversized files produce useful errors.
- [ ] Duplicate clicks do not submit multiple jobs.
- [ ] Downloads contain the same text shown in the preview, including Unicode characters and line breaks.
- [ ] Download attempts for unfinished, failed, or unknown jobs return the specified errors.
- [ ] Network interruptions allow recovery without automatically re-uploading the file.
- [ ] The page works on mobile and can be operated with a keyboard.
