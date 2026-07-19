# Backend API contract

The extension UI is a separate workstream. It can consume this contract without
changing backend behavior.

## Job summary

`POST /jobs` starts a job. `GET /jobs/{job_id}` returns its current summary.

The extension may send `assets` with temporary original-file URLs read from its
authenticated page context. Cookies, passwords, and browser session data are
never part of this payload.

Important counters:

- `discovered`: public audio assets found on the source page.
- `downloaded`: files downloaded successfully.
- `failed`: downloads that failed before local audio analysis.
- `analyzed`: downloaded files for which analysis was attempted.
- `loops`, `one_shots`, `fx`, `unknown`: mutually exclusive classification
  counts. Their sum equals `analyzed` after a completed job.
- `audio_errors`: decoded files rejected by the quality gate plus files that
  could not be decoded/analyzed.
- `rejected`: decoded audio with quality issues.
- `analysis_failed`: files that could not be decoded/analyzed.
- `organization_failed`: files that could not be moved into a category folder.
- `sample_results_total`: number of per-file result records.
- `report_path`: local `sample-report.json` path after the job finishes.

Job status progresses through `queued`, `discovering`, `downloading`,
`analyzing`, then `completed` or `failed`.

## Per-file results

`GET /jobs/{job_id}/samples?offset=0&limit=100`

The endpoint is paginated. `limit` is clamped to `1..500`. The response has:

```json
{
  "job_id": "...",
  "total": 1,
  "offset": 0,
  "limit": 100,
  "items": [
    {
      "file": "Kick.wav",
      "status": "passed",
      "content_type": "one-shot",
      "category": "One-Shots",
      "output": "D:\\Samples\\job\\One-Shots\\Kick.wav",
      "analysis": {
        "duration_sec": 0.42,
        "bpm": 128,
        "key": "C# Minor",
        "bpm_source": "catalogue",
        "key_source": "catalogue",
        "issues": []
      }
    }
  ]
}
```

Per-file status is `passed`, `rejected`, `analysis_failed`, or
`organization_failed`.

## Output layout

Every job has its own output directory. Original downloaded files are preserved
in their original format and moved into one of:

```text
<job>/Loops/
<job>/One-Shots/
<job>/FX/
<job>/Unsorted/
<job>/sample-report.json
```

The classifier follows the existing MH-Dowsample `QualityGate` content contract.

## Browser delivery

- `GET /jobs/{job_id}/files` lists completed audio files.
- `GET /jobs/{job_id}/download/{file_id}` streams one file with its real MIME
  type and filename.
- `POST /jobs/{job_id}/cancel` requests cancellation.

When the server runs on Render, files are temporary. The extension starts one
Chrome/Cốc Cốc download per completed audio file. The server removes terminal
job folders after `MH_JOB_TTL_SECONDS` (30 minutes by default).

## Render runtime and protection

Run with `python backend/server.py` and configure:

- `MH_REMOTE_MODE=true`
- `MH_EXTENSION_ACCESS_KEY=<private extension access code>`
- `MH_ALLOWED_SOURCE_HOSTS=splice.com,.splice.com,splice-res.cloudinary.com`
- `PORT` supplied by Render

Remote requests require `X-MH-Access-Key`. Only extension origins are accepted
for browser API calls. Source URLs are restricted to the allowlist and DNS/IP
checks reject loopback, private, link-local, and other non-public addresses.
