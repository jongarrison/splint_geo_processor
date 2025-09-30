# Splint Geometry Processor

A single-threaded worker that polls Splint Factory for geometry processing jobs, runs a Grasshopper-based pipeline (via Rhino), optionally slices with Bambu Studio, and reports results back to the server.

## Requirements
- Node.js 18+
- Splint Factory server running (default: http://localhost:3000)
- API Key with permissions: `geometry-queue:read` and `geometry-queue:write`
- (Optional) Rhino WIP / Rhinocode CLI and Bambu Studio CLI installed

## Configuration
- Preferred: single JSON at `./secrets/config.json`
	- Example:
		{
			"SPLINT_SERVER_URL": "http://localhost:3000",
			"SPLINT_SERVER_API_KEY": "<raw-api-key>",
			"POLL_INTERVAL_MS": 3000,
			"GH_SCRIPTS_DIR": "./generators",
			"RHINO_CLI": "/Applications/RhinoWIP.app/Contents/MacOS/rhinocode",
			"BAMBU_CLI": "/Applications/BambuStudio.app/.../bambu",
			"DRY_RUN": true
		}
- Environment variables remain supported and override the JSON if set.

## File Conventions
- Inbox: `~/SplintFactoryFiles/inbox`
- Outbox: `~/SplintFactoryFiles/outbox`
- Log files: `~/SplintFactoryFiles/logs`
- Inbox filenames: `{GeometryAlgorithmName}_{GeometryProcessingQueueID}.json`

## Development
- Install deps and run dev:
	- `npm install`
	- `npm run dev`

## Notes
- The worker is single-threaded: it pauses polling while processing a job.
- In DRY_RUN mode, it writes tiny dummy geometry/print files and reports success.
# Splint Factory Geometry Processing System

See ./agent-instructions for system requrements docs

