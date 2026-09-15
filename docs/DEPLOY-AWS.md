# Deploying on AWS

Two pieces: **S3** for plant images, and a host for the app. Either can be
swapped out — the app falls back to local disk without S3, and runs anywhere
that can run a container.

---

## 1. S3 for plant images

### Create a private bucket

The app never makes objects public. Images reach browsers through **presigned
GET URLs** generated at render time, valid for one hour by default. So block
public access entirely:

```bash
aws s3api create-bucket \
  --bucket my-garden-images \
  --region us-east-1

aws s3api put-public-access-block \
  --bucket my-garden-images \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws s3api put-bucket-encryption \
  --bucket my-garden-images \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

### CORS

Only needed if a browser ever fetches an object with JavaScript. Plain
`<img src>` doesn't require it, so start without CORS and add it only if you
build something that does.

### IAM: least privilege

The app needs three actions on one prefix — nothing more. It never lists the
bucket and never touches other prefixes.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PlantImages",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::my-garden-images/plants/*"
    }
  ]
}
```

**On EC2/ECS, attach this as an instance or task role and set no keys at all** —
boto3 picks up the role automatically. Static access keys are a last resort; if
you must use them, they belong in the environment, never in the repo.

```bash
# .env — leave the key variables empty when using an instance role
AWS_S3_BUCKET=my-garden-images
AWS_REGION=us-east-1
STORAGE_BACKEND=s3          # or "auto", which picks S3 whenever a bucket is set
S3_PRESIGN_EXPIRY_SECONDS=3600
```

Verify:

```bash
python manage.py check      # should print "Storage      s3"
```

### Testing S3 without an AWS account

Point `AWS_S3_ENDPOINT_URL` at MinIO or LocalStack:

```bash
docker run -d -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  quay.io/minio/minio server /data --console-address ":9001"
```

```bash
AWS_S3_ENDPOINT_URL=http://localhost:9000
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_S3_BUCKET=garden-images
```

---

## 2. Running the app on EC2

### Instance

`t3.small` is enough for a small deployment (the app is IO-bound: it waits on
Mongo, SMTP and Gemini rather than burning CPU). Amazon Linux 2023 or Ubuntu
22.04+.

Security group: inbound 443 and 80 from anywhere, 22 from your IP only.
**MongoDB's 27017 should never be open to the internet** — use a VPC-internal
address or MongoDB Atlas with an IP allowlist.

### Install

```bash
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user   # log out and back in

sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose

git clone <your-repo-url> gardening-companion
cd gardening-companion
cp .env.example .env
```

Edit `.env`:

```bash
FLASK_ENV=production
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
MONGO_URI=mongodb://user:pass@10.0.1.50:27017/gardening_companion?authSource=admin
APP_BASE_URL=https://garden.example.com
AWS_S3_BUCKET=my-garden-images
GEMINI_API_KEY=<from https://aistudio.google.com/apikey>
MAIL_SERVER=email-smtp.us-east-1.amazonaws.com   # SES
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=<SES SMTP username>
MAIL_PASSWORD=<SES SMTP password>
MAIL_DEFAULT_SENDER=Gardening Companion <no-reply@garden.example.com>
```

`FLASK_ENV=production` makes the app **refuse to start** with a default
`SECRET_KEY`, and turns on `Secure` cookies and HSTS.

```bash
docker compose up -d --build
docker compose exec web python manage.py ensure-indexes
docker compose exec web python manage.py seed-catalog
docker compose exec web python manage.py create-admin
```

Drop `mailhog` from `docker-compose.yml` in production, and remove the SMTP
overrides from the `web`, `worker` and `beat` services so they use your `.env`.

### nginx + TLS

```nginx
server {
    listen 80;
    server_name garden.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name garden.example.com;

    ssl_certificate     /etc/letsencrypt/live/garden.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/garden.example.com/privkey.pem;

    client_max_body_size 10M;   # must exceed MAX_UPLOAD_MB

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;   # required for HTTPS redirects
        proxy_read_timeout 90s;                          # Gemini calls can be slow
    }

    location /healthz {
        proxy_pass http://127.0.0.1:5000/healthz;
        access_log off;
    }
}
```

```bash
sudo dnf install -y certbot python3-certbot-nginx
sudo certbot --nginx -d garden.example.com
```

Behind a proxy, tell Flask to trust the forwarded headers — add to `wsgi.py`:

```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
```

Without this, `url_for(..., _external=True)` builds `http://` links in reset
emails, and rate limiting sees every request as coming from the proxy.

---

## 3. Without Docker: systemd

```ini
# /etc/systemd/system/gardening-web.service
[Unit]
Description=Gardening Companion web
After=network.target

[Service]
User=garden
WorkingDirectory=/opt/gardening-companion
EnvironmentFile=/opt/gardening-companion/.env
ExecStart=/opt/gardening-companion/.venv/bin/gunicorn \
    --bind 127.0.0.1:5000 --workers 3 --threads 4 --timeout 60 \
    --access-logfile - --error-logfile - wsgi:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/gardening-worker.service
[Unit]
Description=Gardening Companion Celery worker
After=network.target

[Service]
User=garden
WorkingDirectory=/opt/gardening-companion
EnvironmentFile=/opt/gardening-companion/.env
ExecStart=/opt/gardening-companion/.venv/bin/celery \
    -A celery_worker:celery worker --loglevel=info --concurrency=2
Restart=always

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/gardening-beat.service
[Unit]
Description=Gardening Companion Celery beat
After=network.target

[Service]
User=garden
WorkingDirectory=/opt/gardening-companion
EnvironmentFile=/opt/gardening-companion/.env
ExecStart=/opt/gardening-companion/.venv/bin/celery \
    -A celery_worker:celery beat --loglevel=info \
    --schedule /var/lib/gardening/celerybeat-schedule
Restart=always

[Install]
WantedBy=multi-user.target
```

**Run exactly one `beat`.** Two would double every scheduled sweep — harmless
here, because `reminder_log` refuses the duplicate claim, but wasteful.

```bash
sudo systemctl enable --now gardening-web gardening-worker gardening-beat
```

---

## 4. Email with SES

1. Verify your sending domain in SES and add the DKIM records.
2. Request production access — the sandbox only sends to verified addresses.
3. Create SMTP credentials (these are *not* your IAM keys) and put them in `.env`.

Reminders arrive as one digest per user per sweep, so volume stays low: a
thousand active users generate at most a thousand messages a day.

---

## 5. Operating it

```bash
python manage.py check                   # every dependency, at a glance
python manage.py send-reminders --dry-run # what the next sweep would send
curl -fsS https://garden.example.com/healthz
docker compose logs -f web worker beat
```

`/healthz` returns 200 when MongoDB is reachable and 503 otherwise — point your
load balancer or uptime monitor at it.

Set `LOG_JSON=true` in production so CloudWatch indexes the fields; every line
carries a request id that is also returned to the client as `X-Request-ID`.

### Backups

```bash
mongodump --uri "$MONGO_URI" --gzip --archive=/backups/garden-$(date +%F).gz
```

S3 objects are already durable; enable versioning if you want protection from
accidental deletes.

### Upgrades

```bash
git pull
docker compose up -d --build
docker compose exec web python manage.py ensure-indexes   # idempotent
```
