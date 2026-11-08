# Third-party licenses

All runtime dependencies are MIT/BSD/Apache-2.0/PSF licensed and compatible with this project's MIT license. Regenerate with `uv run --directory backend pip-licenses` (`uvx pip-licenses --from=mixed`) and `npx license-checker --production` in `frontend/`.

## Backend (Python)

| Package | License |
|---|---|
| fastapi, starlette | MIT |
| uvicorn | BSD-3-Clause |
| pydantic | MIT |
| SQLAlchemy | MIT |
| alembic | MIT |
| platformdirs | MIT |
| ollama (python client) | MIT |
| python-multipart | Apache-2.0 |
| Pillow | MIT-CMU |
| pypdf | BSD-3-Clause |
| python-docx | MIT |
| pytesseract (optional) | Apache-2.0 (Tesseract itself: Apache-2.0) |
| pytest, httpx, ruff, mypy (dev) | MIT / BSD / MIT / MIT |

## Frontend (JavaScript)

| Package | License |
|---|---|
| react, react-dom | MIT |
| react-router-dom | MIT |
| vite, @vitejs/plugin-react | MIT |
| tailwindcss, @tailwindcss/vite | MIT |
| typescript | Apache-2.0 |
| vitest, @testing-library/* , jsdom (dev) | MIT |
| @playwright/test (dev) | Apache-2.0 |

Ollama (the server) is MIT licensed; models have their own licenses (check `ollama show <model>`).
