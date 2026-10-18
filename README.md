# Pengu-learn

A local desktop coding tutor with a Python daemon, a React/Tauri interface, and a FalkorDB knowledge graph. The graph connects lessons, programming concepts, prerequisite relationships, common mistakes, and student progress. The daemon and database bind to loopback by default.

## Prerequisites

- Python 3.10 or newer
- Node.js and npm
- Docker with the Compose plugin, for FalkorDB
- Ollama is optional. Without it, the tutor falls back to built-in prompts and heuristics.

## Setup

From the repository root, start FalkorDB:

```sh
docker compose up -d falkordb
```

The Compose file publishes FalkorDB's Redis-compatible endpoint on `127.0.0.1:6379` and its browser on `127.0.0.1:3000`, with data persisted in a Docker volume.

Install the Python dependencies and FalkorDB client:

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux:       source .venv/bin/activate
python -m pip install -r daemon/requirements.txt
python -m pip install falkordb
```

Install the UI dependencies:

```sh
cd ui
npm ci
cd ..
```

Optionally install and start Ollama, then fetch the configured tutor model:

```sh
ollama pull qwen2.5-coder:7b
```

Start the complete stack with the helper script:

```sh
# Windows PowerShell
.\start.ps1

# macOS/Linux
./start.sh
```

The scripts check for FalkorDB, initialize the concept graph, start the daemon, and run the UI in development mode if no built desktop app is available. The daemon listens at `http://127.0.0.1:7777`; the Vite UI is at `http://localhost:5173` (dashboard: `/dashboard.html`). To run only the daemon after starting FalkorDB, use:

```sh
cd daemon
python -m uvicorn app:app --host 127.0.0.1 --port 7777
```

The daemon uses the graph named `socratic_tutor` by default and initializes its indexes on connection. It seeds a starter student and sample graph data if that graph has no `local-user` student yet. Lesson and concept definitions live under `lessons/`.

## FalkorDB usage

FalkorDB uses the Redis protocol on port `6379`. Open `http://127.0.0.1:3000` for the FalkorDB Browser and run Cypher against graph `socratic_tutor`. For example:

```cypher
MATCH (n)
RETURN labels(n) AS labels, count(*) AS count
```

```cypher
MATCH (s:Student {id: 'local-user'})-[m:MASTERED]->(c:Concept)
RETURN c.id AS concept, m.score AS mastery, m.due_date AS due_date
ORDER BY mastery ASC
```

```cypher
MATCH (s:Student {id: 'local-user'})-[:ATTEMPTED]->(a:Attempt)-[:FOR_LESSON]->(l:Lesson)
OPTIONAL MATCH (a)-[:MADE_MISTAKE]->(m:Mistake)
RETURN a.timestamp, l.title, a.passed, m.id
ORDER BY a.timestamp DESC
```

Python code can use the same graph through the repository helpers:

```python
from graph.schema import get_client, get_graph, init_schema

client = get_client(host="localhost", port=6379)
graph = get_graph(client, graph_name="socratic_tutor")
init_schema(graph)  # safe to call more than once
result = graph.query("MATCH (c:Concept) RETURN c.id, c.name LIMIT 10")
print(result.result_set)
```

Run that snippet from `daemon/` (or set `PYTHONPATH=daemon` from the repository root). `daemon/graph/ingest.py` contains the lesson ingestion, attempt recording, and mastery update functions. Lesson ingestion reads each lesson's `lesson.yaml` and optional `README.md`. The UI reads progress and graph data through the daemon API; normally, the app handles these writes for you.

Useful lifecycle commands:

```sh
docker compose logs -f falkordb
docker compose stop falkordb
docker compose down                 # retains the named data volume
docker compose down -v              # deletes graph data in the volume
```

## Graph data model

Node identity is the `id` property. Schema initialization creates uniqueness constraints for `Student`, `Lesson`, `Concept`, and `Mistake`, and a vector index on `Chunk.embedding` (768 dimensions, cosine similarity).

| Node | Role and typical properties |
| --- | --- |
| `Student` | Learner profile: `id`, `name` |
| `Lesson` | Curriculum unit: `id`, `title`, `track`, `difficulty`, `estimated_minutes`, `languages` |
| `Concept` | Skill or topic: `id`, `name`, `track` |
| `Mistake` | Recognized misconception: `id`, `signals`, `seed_questions` |
| `Chunk` | Text excerpt for retrieval: `id`, `content`, `lesson_id`, `chunk_index`, optional `embedding` |
| `Attempt` | A student submission: `id`, `student_id`, `lesson_id`, `passed`, `timestamp` |

Relationships encode curriculum and learning history:

| Relationship | Direction | Meaning |
| --- | --- | --- |
| `REQUIRES` | `Concept → Concept` | The source concept depends on the target prerequisite |
| `TEACHES` | `Lesson → Concept` | The lesson teaches the concept |
| `REQUIRES` | `Lesson → Concept` | The lesson assumes the prerequisite concept |
| `INVOLVES` | `Mistake → Concept` | The misconception relates to this concept |
| `EXPLAINS` | `Chunk → Lesson` | The chunk contains explanatory material for the lesson |
| `ATTEMPTED` | `Student → Attempt` | The learner made this attempt |
| `FOR_LESSON` | `Attempt → Lesson` | The attempt belongs to the lesson |
| `MADE_MISTAKE` | `Attempt → Mistake` | The attempt exhibited this mistake (optional) |
| `MASTERED` | `Student → Concept` | Mastery state stored on the relationship |

`MASTERED` relationships store `score` (0–1), `streak`, `review_count`, `last_reviewed`, `due_date`, `interval_hours`, and `last_passed`. A passing result adds 0.10 and a failure subtracts 0.15, clamped to the 0–1 range; the graph ingestion code calculates the next review time from the score and successful recall streak.

Lesson packages are defined by `lessons/<lesson-id>/lesson.yaml`. The shared concept prerequisite graph is `lessons/concepts.yaml`; concept prerequisite edges point from the dependent concept to what it requires. `daemon/graph/ingest.py` ingests lesson metadata, concepts, mistakes, and README chunks. Explanation embeddings use Ollama's `nomic-embed-text` model when available; offline fallback embeddings allow ingestion to continue, but meaningful semantic similarity requires real embeddings.

## Configuration and local data

- `config.yaml` sets the daemon address, FalkorDB connection, optional Ollama model, and tutor behavior.
- `docker-compose.yml` controls the local database ports and persistent volume.
- Student history also has a local cache at `~/.cache/tutor-pet/student_history.json` (on Windows, under the user profile cache directory).
- Voice input is optional and disabled by default; see `daemon/requirements-voice.txt` for its extra Python packages.
