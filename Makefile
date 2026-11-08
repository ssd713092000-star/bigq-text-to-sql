# Linux/macOS/CI equivalent of scripts/dev.ps1
COMPOSE = docker compose -f infra/docker-compose.yaml --env-file infra/.env

.PHONY: up down reset logs ps migrate test seed evals
up:      ; $(COMPOSE) up --build -d && $(COMPOSE) ps
down:    ; $(COMPOSE) down
reset:   ; $(COMPOSE) down -v
logs:    ; $(COMPOSE) logs -f $(s)
ps:      ; $(COMPOSE) ps
migrate: ; $(COMPOSE) run --rm migrate
test:    ; uv run ruff check api mcp_server && uv run pytest -q
seed:    ; @echo "seed arrives in phase 4"
evals:   ; @echo "evals against the stack arrive in phase 8"
