# Deploying Open WebUI to voorbeeld.soev.ai

## Prerequisites

1. Ubuntu VM (22.04 LTS recommended)
2. Docker and Docker Compose installed
3. DNS A record pointing `voorbeeld.soev.ai` to VM's public IP
4. Ports 80 and 443 open in firewall

## Quick Start

### 1. Install Docker (if not installed)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

### 2. Clone Repository

```bash
git clone https://github.com/gradient-ds/open-webui.git
cd open-webui
```

### 3. Configure Environment

```bash
# Copy and edit demo environment
cp .env.demo.example .env.demo

# Generate secret key
echo "WEBUI_SECRET_KEY=$(openssl rand -hex 32)" >> .env.demo

# Edit and set:
# - DATABASE_PASSWORD (strong password)
# - OPENAI_API_KEY (your API key)
nano .env.demo
```

### 4. Start Services

```bash
docker compose -f docker-compose.demo.yaml up -d
```

### 5. Verify Deployment

```bash
# Check all containers are healthy
docker compose -f docker-compose.demo.yaml ps

# View logs
docker compose -f docker-compose.demo.yaml logs -f

# Test health endpoint
curl https://voorbeeld.soev.ai/health
```

### 6. Create Admin Account

1. Open https://voorbeeld.soev.ai in browser
2. Click "Sign up" (only works for first user)
3. Create your admin account

## Updating

```bash
# Pull latest image and restart
docker compose -f docker-compose.demo.yaml pull
docker compose -f docker-compose.demo.yaml up -d
```

## Backup

```bash
# Backup database
docker exec demo-postgres pg_dump -U openwebui openwebui > backup.sql

# Backup volumes
docker run --rm -v demo-data:/data -v $(pwd):/backup alpine tar czf /backup/data-backup.tar.gz -C /data .
```

## Troubleshooting

### Check logs
```bash
docker compose -f docker-compose.demo.yaml logs open-webui
docker compose -f docker-compose.demo.yaml logs caddy
```

### SSL Certificate Issues
Caddy handles certificates automatically. If issues:
```bash
# Check Caddy logs
docker compose -f docker-compose.demo.yaml logs caddy

# Verify DNS is propagated
dig voorbeeld.soev.ai
```

### Database Connection Issues
```bash
# Check postgres is healthy
docker compose -f docker-compose.demo.yaml ps postgres

# Connect to postgres
docker exec -it demo-postgres psql -U openwebui -d openwebui
```

## Staging Environment

For local development/testing:

```bash
# Copy staging environment template
cp .env.staging.example .env.staging

# Start staging stack
docker compose -f docker-compose.staging.yaml up -d

# Access at http://localhost:3000
```
