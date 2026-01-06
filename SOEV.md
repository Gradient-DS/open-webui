To start development:

(all in the root)

## Environment
copy .env.neo.example to .env
fill in at least an openai api key

## Serviced
docker compose -f docker-compose.neo-dev.yaml up -d

## Backend
python3.11 -m venv .venv
Activate venv 

pip install -e ".[soev]" 

open-webui dev (starts the backend server)

## Frontend
npm install --legacy-peer-deps
npm run dev
Should be available on localhost:5173