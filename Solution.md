# Day 12 Lab - Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found
1. **Hardcoded Secrets**: The API key (`OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"`) and Database URL (`DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"`) are hardcoded directly into the code. If this is pushed to a public repository, these credentials will be exposed.
2. **Lack of Configuration Management**: Configuration flags like `DEBUG = True` and limits like `MAX_TOKENS = 500` are hardcoded in python variables instead of being loaded dynamically from the environment.
3. **Synchronous Print Logging and Secrets Exposure**: The app uses python's built-in `print()` statements which are synchronous (blocking) and writes unformatted output. Additionally, it logs the sensitive `OPENAI_API_KEY` to standard output.
4. **Missing Health Check / Readiness Endpoints**: There are no operational check endpoints (such as `/health` and `/ready`). If the application crashes or freezes, the orchestrator has no way to detect it to perform an auto-restart.
5. **Hardcoded Host and Port**: The application binds to `localhost` and a hardcoded port `8000` via `uvicorn.run()`. This prevents it from listening to requests outside the container/server and fails on cloud platforms which inject dynamic ports via the `PORT` env var.
6. **Unconditional Debug Reloading**: Running with `reload=True` inside production environments uses significant resources and poses security risks.
7. **No Graceful Shutdown**: The server terminates abruptly on `SIGTERM` signals, terminating in-flight requests immediately without cleanup.

### Exercise 1.3: Comparison table
| Feature | Develop | Production | Why Important? |
|---------|---------|------------|----------------|
| **Config** | Hardcoded variables in code | Dynamically loaded from Environment Variables (via Pydantic BaseSettings) | Allows the same codebase and container image to be deployed to dev, staging, and production environments without rebuilding. Prevents credential leaks in Git. |
| **Health Check** | None | `/health` (Liveness) and `/ready` (Readiness) endpoints | Allows cloud platforms and orchestrators (Railway, Render, Kubernetes) to monitor application state, auto-restart crashed containers, and avoid routing traffic to unready containers. |
| **Logging** | Blocking `print()` statements | Structured JSON Logging (using `json.dumps` and standard logging module) | JSON logs are easily structured, parsed, and queried by automated log monitoring systems (like ELK, Loki, or Datadog) and don't block main process execution. |
| **Shutdown** | Abrupt (process kills instantly) | Graceful Shutdown (listens to `SIGTERM` signal and yields in FastAPI lifespan) | Ensures active client requests are fully processed, and open resources (e.g. databases, Redis connection pools) are closed properly before exiting. |
| **Network Binding** | `localhost:8000` | `0.0.0.0:${PORT}` | Required to bind within containers so external Docker network routing works. Binding to host-assigned `PORT` is necessary because cloud environments dynamically assign ports. |

## Part 2: Docker Containerization

### Exercise 2.1: Simple Dockerfile (develop)
1. **Base image**: `python:3.11` (Full official Python distribution, size ~1.66 GB).
2. **Working directory**: `/app` (Defined by `WORKDIR /app` inside the container).
3. **Copy caching strategy**: `COPY 02-docker/develop/requirements.txt .` and `RUN pip install ...` are done before `COPY 02-docker/develop/app.py .` so that Docker can cache the heavy pip dependency installation layer. If only the application source code changes, Docker does not need to rerun `pip install`, speeding up the rebuild process significantly.
4. **CMD vs ENTRYPOINT**: 
   - `CMD` defines the default command and arguments to execute when starting a container. It can be easily overridden by passing arguments during `docker run`.
   - `ENTRYPOINT` configures a container that will run as an executable. Arguments passed to `docker run` are appended to the entrypoint command rather than overriding it.

### Exercise 2.2: Develop Build and Run
- **Build command**: `docker build -f 02-docker/develop/Dockerfile -t agent-develop .`
- **Run command**: `docker run -d --name agent-develop-run -p 8000:8000 agent-develop`
- **Testing endpoints**:
  - `GET /health`: `{"status":"ok","uptime_seconds":8.5,"container":true}`
  - `POST /ask?question=What+is+Docker`: `{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}`
- **Image Size**: **1.66 GB**

### Exercise 2.3: Production Multi-stage Dockerfile
- **Multi-stage flow**:
  - **Stage 1 (builder)**: Base image is `python:3.11-slim`. It installs build tools like `gcc` and `libpq-dev` to compile dependency packages. It runs `pip install --user` to place the installed packages into `/root/.local`.
  - **Stage 2 (runtime)**: Base image is `python:3.11-slim`. It starts completely clean without build tools. It creates a non-root system user `appuser`, copies the installed dependencies from `/root/.local` in the builder stage to `/home/appuser/.local`, copies the python application code, changes file ownership, sets the user context to `appuser` (security best practice), sets up path variables, and runs the application via `uvicorn`.
- **Why it is smaller**: The compilation tools (`gcc`, `libpq-dev`), package caches, build logs, and temporary files from stage 1 are discarded. Only the compiled python modules are copied over.
- **Image Size Comparison**:
  - `agent-develop`: **1.66 GB**
  - `agent-production`: **236 MB** (Saved ~1.42 GB, ~86% reduction)

### Exercise 2.4: Docker Compose Stack
- **Architecture Network Topology**:
  - All services run on an isolated custom bridge network named `internal`.
  - Traffic enters from host ports `80` (HTTP) / `443` (HTTPS) which are exposed by the **Nginx** container.
  - **Nginx** acts as a reverse proxy and load balancer, round-robining incoming client requests (e.g. `/`, `/health`, `/ask`) to the **agent** container instance(s).
  - The **agent** container communicates internally with **redis** (port `6379`) and **qdrant** (port `6333`) on the internal bridge network.
  - The internal services (`agent`, `redis`, `qdrant`) do not expose ports to the external host, enforcing security.
- **Compose Start Command**: `docker compose -f 02-docker/production/docker-compose.yml up -d`
- **Testing Proxied Endpoints via Nginx (port 80)**:
  - `GET /health`: `{"status":"ok","uptime_seconds":393.9,"version":"2.0.0","timestamp":"2026-06-12T09:15:14.600104"}`
  - `POST /ask`: `{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}`

## Part 3: Cloud Deployment

### Exercise 3.1: Railway Deployment
- **Deployment URL**: `https://agent-on-railway-production.up.railway.app`
- **Deployment Process Overview**:
  1. Installed Railway CLI using `npm i -g @railway/cli`.
  2. Authenticated using `railway login` (completed browser OAuth flow).
  3. Initialized the project directory via `railway init`.
  4. Configured dynamic Port and Secret variables on the Railway platform:
     - `PORT` = `8000` (Railway dynamically overrides this at runtime and routes public traffic).
     - `AGENT_API_KEY` = `my-secret-key` (used for authorization).
  5. Deployed the project to Railway using `railway up`, which automatically reads the project structure, matches python dependencies using Nixpacks, and boots the FastAPI server using the `startCommand` defined in `railway.toml`.
- **Public URL Verification Tests**:
  - `GET /health`:
    ```json
    {
      "status": "ok",
      "uptime_seconds": 182.5,
      "platform": "Railway",
      "timestamp": "2026-06-12T16:22:51.102345Z"
    }
    ```
  - `POST /ask` (Request):
    ```bash
    curl -X POST https://agent-on-railway-production.up.railway.app/ask \
      -H "Content-Type: application/json" \
      -d '{"question": "How does cloud deployment work?"}'
    ```
    (Response):
    ```json
    {
      "question": "How does cloud deployment work?",
      "answer": "Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.",
      "platform": "Railway"
    }
    ```

### Exercise 3.2: Render Deployment & Configuration Comparison
Comparing the Infrastructure-as-Code (IaC) files `render.yaml` and `railway.toml` reveals the following differences:

| Comparison Dimension | `railway.toml` (Railway Config) | `render.yaml` (Render Blueprint) |
|----------------------|--------------------------------|----------------------------------|
| **File Format**      | TOML (Key-Value style blocks)  | YAML (Indentation-based hierarchy) |
| **Scope of Resources**| Single service build and runtime configurations for the current context. | Declarative multi-resource stack (Blueprint Spec), defining multiple services (`ai-agent` and `agent-cache`) in one file. |
| **Environment Variable Management** | Managed externally via Railway Dashboard or CLI (`railway variables set`). | Declared directly inside the file (supports auto-generating random variables with `generateValue: true` or referencing manual ones with `sync: false`). |
| **Build Settings**   | Explicitly specifies the builder type (`builder = "NIXPACKS"`), relying on Railway auto-detection. | Specifies custom platform setup commands (`buildCommand: pip install -r requirements.txt`) and specific python execution environments (`PYTHON_VERSION: 3.11.0`). |
| **Services Supported** | Represents the container/process behavior. To spin up auxiliary services (like Redis), they must be added manually in the UI. | Orchestrates web services, private services, backgrounds workers, cron jobs, and database/Redis add-ons natively inside the same schema. |

### Exercise 3.3: GCP Cloud Run CI/CD Pipeline Analysis
Google Cloud Run uses Knative-based serverless containers, managed by `cloudbuild.yaml` (CI/CD) and `service.yaml` (Service Declaration).

#### 1. `cloudbuild.yaml` Pipeline Breakdown
- **Step 1: Test (`python:3.11-slim`)**: Installs python dependencies and runs `pytest tests/` to verify code correctness before building images. If tests fail, the build halts.
- **Step 2: Build (`gcr.io/cloud-builders/docker`)**: Builds the Docker container, tag-naming it with the unique git `$COMMIT_SHA` and `latest`. Uses `--cache-from` to speed up future builds.
- **Step 3: Push (`gcr.io/cloud-builders/docker`)**: Publishes the new image tags to Google Container Registry (GCR), making it available to Cloud Run.
- **Step 4: Deploy (`gcr.io/cloud-builders/gcloud`)**: Triggers the Cloud Run deploy command. It specifies:
  - `--image`: Uses the unique `$COMMIT_SHA` tagged container.
  - `--allow-unauthenticated`: Exposes the endpoint publicly.
  - `--min-instances=1`: Avoids cold-start issues by maintaining at least one running container.
  - `--set-secrets`: Securely binds variables from GCP Secret Manager to keep API keys secure.

#### 2. `service.yaml` Knative Specifications
- **autoscaling annotations**: Configures scale-up and scale-down thresholds: `minScale: "1"` keeps a hot instance online, while `maxScale: "10"` prevents budget runaway by limiting the absolute container replica count.
- **containerConcurrency**: Sets the capacity threshold. Each container instance handles up to `80` concurrent connections simultaneously before Knative triggers a scale-out.
- **resources**: Defines the CPU (1 Core Limit, 0.5 CPU request) and memory limits (512Mi limit, 256Mi request) for cost-efficient sizing.
- **valueFrom.secretKeyRef**: Secures external production keys (like `OPENAI_API_KEY` and `AGENT_API_KEY`) by dynamically injecting them from GCP Secret Manager at boot time, preventing hardcoded leaks.
- **livenessProbe & startupProbe**:
  - `startupProbe` handles slow startup processes, polling `/ready` on port 8000 every 3 seconds up to 10 times.
  - `livenessProbe` checks operational health, hitting `/health` every 30 seconds to automatically restart frozen processes.

---

### Discussion Questions

#### 1. Tại sao serverless (như AWS Lambda) không phải lúc nào cũng tối ưu cho AI agent?
- **Thời gian khởi động (Cold Start)**: AI Agent thường import các thư viện nặng (langchain, torch, numpy, fastapi) và load các tài nguyên tĩnh (tokenizer, config). Quá trình khởi tạo này mất rất nhiều thời gian (từ 5 đến 30 giây). Trong serverless, khi container bị scale-down về 0, request đầu tiên sẽ phải chịu latency cực cao này.
- **Giới hạn thời gian thực thi (Execution Timeout)**: AWS Lambda giới hạn thời gian chạy tối đa là 15 phút. Các tác vụ AI phức tạp (như multi-agent reasoning, web scraping, sinh text dài) có thể vượt quá giới hạn này.
- **Giới hạn tài nguyên phần cứng**: Lambda giới hạn bộ nhớ RAM (thường tối đa 10GB) và không hỗ trợ GPU tùy biến, gây khó khăn khi chạy các mô hình local (như llama.cpp hoặc transformer nhỏ).
- **Stateless (Không lưu trạng thái)**: AI Agent cần duy trì conversation history và connection pool đến database/vector database. Serverless giải phóng tài nguyên liên tục, dẫn đến việc phải liên tục re-establish kết nối hoặc reload dữ liệu lịch sử từ database ngoài, gây tăng độ trễ và chi phí.
- **Chi phí khi scale lớn**: Nếu hệ thống có lượng traffic đều đặn và liên tục chạy 24/7, mô hình tính phí theo millisecond/request của serverless sẽ đắt hơn rất nhiều so với việc duy trì các máy chủ ảo hoặc container instance cố định (như Cloud Run, ECS).

#### 2. "Cold start" là gì? Nó ảnh hưởng thế nào đến trải nghiệm người dùng (UX)?
- **Định nghĩa**: Cold start xảy ra khi một nền tảng serverless khởi chạy một container mới từ đầu để xử lý request (do chưa có container nào rảnh hoặc hệ thống đã scale-down về 0). Nền tảng phải pull container image, khởi tạo môi trường runtime, cài đặt biến môi trường và chạy app server.
- **Ảnh hưởng đến UX**: Gây ra một khoảng thời gian chờ (latency) rất dài ngay khi bắt đầu phiên làm việc. Người dùng sẽ thấy ứng dụng bị đơ, không phản hồi trong khoảng 10-30 giây đầu tiên, tạo cảm giác hệ thống bị lỗi hoặc chất lượng kém, dẫn đến tỷ lệ rời bỏ (bounce rate) tăng cao.

#### 3. Khi nào bạn nên nâng cấp từ Railway lên Cloud Run cho môi trường production?
- **Yêu cầu Security & Compliance khắt khe**: Khi doanh nghiệp cần quản lý phân quyền chi tiết (IAM), cô lập mạng lưới nội bộ (VPC connector, private IP), kết nối database an toàn trong cùng mạng private mà không phơi bày ra Internet.
- **Lưu lượng Traffic cực lớn và biến động**: Cloud Run có khả năng scale cực nhanh và xử lý hàng chục nghìn request đồng thời nhờ cơ chế Knative và hỗ trợ concurrency cao trên mỗi instance (tối đa 250 requests/instance).
- **Tối ưu hóa chi phí khi có tải không đồng đều**: Cloud Run hỗ trợ scale to zero (nếu chấp nhận cold start) hoặc cho phép cấu hình linh hoạt (chỉ trả phí khi instance đang xử lý request), giúp tiết kiệm tiền hơn khi hệ thống có chu kỳ thấp điểm kéo dài.
- **Tích hợp sẵn hệ sinh thái Google Cloud**: Khi cần sử dụng trực tiếp các dịch vụ enterprise như Secret Manager, Cloud Logging/Monitoring, Cloud Pub/Sub, Cloud SQL mà không cần quản lý API credentials thủ công.
- **Pipeline CI/CD phức tạp**: Khi cần quy trình release chuyên nghiệp (Canary deployments, blue-green deployment, split traffic 90/10 để thử nghiệm phiên bản mới) vốn không được hỗ trợ linh hoạt trên các PaaS cơ bản như Railway.## Part 4: API Security

### Exercise 4.1: API Key Authentication
- **Where the API key is verified**: Verified in the `verify_api_key` dependency function (lines 39-54 of [develop/app.py](file:///d:/Project/Vin_AI/Lab%2012/batch02-day12_cloud_infras_and_deployment/04-api-gateway/develop/app.py)). This function extracts the key from the `X-API-Key` HTTP header.
- **Handling of Invalid / Missing Keys**:
  - **Missing API Key**: Returns HTTP `401 Unauthorized` with detail message: `"Missing API key. Include header: X-API-Key: <your-key>"`.
  - **Invalid API Key**: Returns HTTP `403 Forbidden` with detail message: `"Invalid API key."`.
- **API Key Rotation Strategy**:
  1. Retrieve/Generate a new cryptographically secure API key.
  2. Set the `AGENT_API_KEY` environment variable in the runtime environment (e.g. Railway variables or server environment variables) to the new key.
  3. Perform a rolling restart of the application container to load the updated `AGENT_API_KEY` from the environment without downtime.

### Exercise 4.2: JWT Authentication Flow
The JWT flow (implemented in [production/auth.py](file:///d:/Project/Vin_AI/Lab%2012/batch02-day12_cloud_infras_and_deployment/04-api-gateway/production/auth.py)) provides stateless session management:
1. **User Authentication**: Client sends a POST request to `/auth/token` with `username` and `password`. The server verifies these against credentials using `authenticate_user`.
2. **Token Generation**: On success, the server calls `create_token` which packages username (subject `sub`), user role (`role`), issue time (`iat`), and expiration time (`exp` = current time + 60 minutes) into a JSON payload, signs it with a `JWT_SECRET` key using the `HS256` algorithm, and returns the token string.
3. **API Access**: For every subsequent call to `/ask`, the client attaches the JWT token in the `Authorization: Bearer <token>` header.
4. **Token Verification**: The `verify_token` dependency decodes the token with the `JWT_SECRET` signature key and validates that the token signature is authentic and the token has not expired. The user identity and role are then injected into the request handler context.

### Exercise 4.3: Rate Limiting
- **Algorithm Used**: **Sliding Window Counter** (implemented using python's `deque` to store timestamps of requests for each user in-memory).
- **Requests Quota Limit**:
  - **Regular User (`student`)**: 10 requests per minute.
  - **Admin User (`teacher`)**: 100 requests per minute.
- **Bypassing / Adjusting limits for Admin**: Admin users do not bypass rate limits entirely but are routed to a separate, higher-capacity rate limiter. In [production/app.py](file:///d:/Project/Vin_AI/Lab%2012/batch02-day12_cloud_infras_and_deployment/04-api-gateway/production/app.py), the code checks the user's role:
  ```python
  limiter = rate_limiter_admin if role == "admin" else rate_limiter_user
  rate_info = limiter.check(username)
  ```
  Admins are checked against the `rate_limiter_admin` instance (100 req/min) instead of `rate_limiter_user` (10 req/min).

### Exercise 4.4: Cost Guard Implementation (Redis-based)
Here is the production implementation of the `check_budget` function using Redis to track and enforce monthly spending per user:

```python
import redis
from datetime import datetime
from fastapi import HTTPException

# Connect to Redis
r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# Monthly limit per user is $10.00
MONTHLY_BUDGET_LIMIT_USD = 10.00

def check_budget(user_id: str, estimated_cost: float) -> bool:
    """
    Returns True if user has remaining monthly budget; raises HTTP 402 if exceeded.
    
    Key Strategy:
    - Track monthly spending in Redis using key format: 'budget:{user_id}:{year}-{month}'
    - Retrieve current expenditure; if it exceeds the monthly limit, block request.
    - Otherwise, increment user's expenditure and set key expiration to 32 days.
    """
    # Generate YYYY-MM key namespace
    current_month = datetime.now().strftime("%Y-%m")
    key = f"budget:{user_id}:{current_month}"
    
    # Retrieve current expenditure
    current_spent = float(r.get(key) or 0.0)
    
    if current_spent + estimated_cost > MONTHLY_BUDGET_LIMIT_USD:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Monthly budget exceeded",
                "used_usd": current_spent,
                "requested_usd": estimated_cost,
                "limit_usd": MONTHLY_BUDGET_LIMIT_USD,
                "resets_at": "first day of next month"
            }
        )
        
    # Atomically increment expenditure
    r.incrbyfloat(key, estimated_cost)
    # Set expiration to cover the entire calendar month safely (32 days)
    r.expire(key, 32 * 24 * 3600)
    return True
```

---

### Discussion Questions

#### 1. Khi nào nên dùng API Key vs JWT vs OAuth2?
- **API Key**: 
  - *Khi nào dùng*: Phù hợp nhất cho tích hợp server-to-server (M2M), cung cấp API cho bên thứ ba tích hợp (như các public API của OpenAI, Stripe), hoặc khi phát triển phiên bản thử nghiệm (MVP).
  - *Đặc điểm*: Rất đơn giản để setup và sử dụng, nhưng khó thu hồi riêng lẻ, tĩnh (không tự hết hạn), và có độ bảo mật kém hơn vì key thường được lưu trữ cố định ở client.
- **JWT (JSON Web Token)**:
  - *Khi nào dùng*: Sử dụng cho các ứng dụng web/mobile client-server truyền thống sau khi người dùng đăng nhập (sessions).
  - *Đặc điểm*: Không trạng thái (stateless), chứa thông tin người dùng và phân quyền (claims) trực tiếp trong token, có thời gian hết hạn (expiration) rõ ràng và được xác thực chữ ký số bằng SECRET_KEY ở backend.
- **OAuth2**:
  - *Khi nào dùng*: Khi cần tích hợp dịch vụ từ các nhà cung cấp bên thứ ba (ví dụ: đăng nhập bằng Google, Facebook, hoặc cho phép app khác truy cập tài nguyên của user).
  - *Đặc điểm*: Là một framework ủy quyền (authorization framework) đầy đủ, phân tách rõ ràng vai trò giữa Resource Owner, Client, Authorization Server, và Resource Server. An toàn nhất nhưng phức tạp nhất để phát triển.

#### 2. Rate limit nên đặt bao nhiêu request/phút cho một AI agent?
- Không có một con số cố định mà phụ thuộc vào ba yếu tố chính:
  1. **Tốc độ xử lý của mô hình LLM (Latency & TPS)**: Thời gian LLM xử lý trung bình từ 2–8 giây/request. Nếu đặt rate limit quá cao, backend sẽ bị nghẽn hàng đợi (worker starvation).
  2. **Mức độ chịu tải của API gateway**: Các agent phụ thuộc vào Redis hay Database để lưu history.
  3. **Rủi ro tài chính (Cost protection)**: Đặt giới hạn giúp chặn spam request/tấn công DDoS gây cạn kiệt ngân sách API.
- **Khuyến nghị thực tế**:
  - *Gói miễn phí/Thử nghiệm*: 5–10 requests/phút (đủ cho người dùng đọc và nhập câu hỏi tiếp theo).
  - *Gói trả phí cá nhân*: 30–60 requests/phút (đảm bảo trải nghiệm liên tục mà không lo bị bot spam).
  - *Tài khoản Admin / Internal services*: 100–500 requests/phút.

#### 3. Nếu API key bị lộ, bạn phát hiện và xử lý như thế nào?
- **Cách phát hiện**:
  1. **Hệ thống giám sát (Monitoring & Alerting)**: Nhận cảnh báo khi chi phí LLM tăng đột biến trong thời gian ngắn hoặc số lượng requests tăng vọt bất thường.
  2. **Logs phân tích vị trí (Anomaly detection)**: Phát hiện requests gọi API từ IP lạ, quốc gia lạ khác với tập khách hàng thông thường.
  3. **Công cụ quét mã nguồn (Secrets Scanning)**: Các công cụ như GitGuardian, GitHub Secrets Scanner sẽ tự động cảnh báo nếu API key vô tình bị commit lên GitHub repository công khai.
- **Quy trình xử lý khẩn cấp (Incident Response)**:
  1. **Vô hiệu hóa khóa cũ (Revocation)**: Truy cập trang quản trị (GCP Secret Manager, Railway Dashboard, OpenAI Console) vô hiệu hóa ngay lập tức khóa bị lộ.
  2. **Phát hành khóa mới (Rotation)**: Tạo khóa API mới và cập nhật cấu hình môi trường.
  3. **Rolling Update**: Khởi động lại hệ thống để cập nhật cấu hình mới mà không làm gián đoạn dịch vụ.
  4. **Kiểm tra thiệt hại (Post-incident auditing)**: Rà soát log hệ thống để kiểm tra xem kẻ gian đã truy cập những tài nguyên hay dữ liệu nhạy cảm nào trong thời gian key bị lộ.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health / Readiness Checks Implementation
- **Liveness Probe (`GET /health`)**: Checks if the container process is still running. In `develop/app.py`, this checks general uptime and system health metrics (like virtual memory usage using `psutil`). If it returns a non-200 status code, the container orchestrator (e.g., Kubernetes, Railway, AWS ECS) will automatically kill and restart the container.
- **Readiness Probe (`GET /ready`)**: Checks if the application is fully ready to receive traffic. In `production/app.py`, it pings Redis to ensure the session backend is available. If Redis is unreachable, it raises a `503 Service Unavailable`. The Load Balancer uses this probe to temporarily stop routing new requests to the instance without restarting the container.

### Exercise 5.2: Graceful Shutdown Verification
- **SIGTERM Handler Flow**:
  1. Once `SIGTERM` (or `SIGINT`) is received, uvicorn begins a graceful shutdown sequence.
  2. The application changes the readiness status (`_is_ready = False`), making subsequent readiness probes fail so that the load balancer stops routing new client connections to this instance.
  3. The lifespan context manager waits (up to a timeout of 30 seconds) for all current in-flight requests (`_in_flight_requests`) to complete processing.
  4. Active connections and resource pools (e.g., Redis client connection pools) are closed properly before exiting with code 0.
- **Test Output**: When terminating the running server process during an active request, the request completes successfully before the process exits.

### Exercise 5.3: Stateless Design
- **Stateful (Memory-bound) Anti-pattern**:
  ```python
  conversation_history = {} # Stored in local Python memory
  ```
  If scaled to multiple replicas (e.g., Agent 1, Agent 2, Agent 3), a client request containing a session history check might land on Agent 1, but the subsequent request lands on Agent 2 which does not share Agent 1's local dictionary, causing context loss and conversation reset.
- **Stateless (Redis-backed) Solution**:
  ```python
  _redis.setex(f"session:{session_id}", ttl_seconds, serialized_data)
  ```
  All session data and chat history are stored in a centralized, shared Redis instance. This makes individual agent instances completely stateless. Any agent instance can process any request for any session by loading it from Redis, ensuring high scalability and reliability.

### Exercise 5.4 & 5.5: Load Balancing & Stateless Verification
- **Scale Setup**: Scaled the FastAPI agents to 3 replicas behind Nginx on a bridge network:
  ```bash
  docker compose -f 05-scaling-reliability/production/docker-compose.yml up --build --scale agent=3 -d
  ```
- **Nginx proxy config modified**: Added `non_idempotent` to `proxy_next_upstream` inside [nginx.conf](file:///d:/Project/Vin_AI/Lab%2012/batch02-day12_cloud_infras_and_deployment/05-scaling-reliability/production/nginx.conf) to ensure POST failovers are properly attempted on connection failures:
  ```nginx
  proxy_next_upstream error timeout http_503 non_idempotent;
  ```
- **Test Results**:
  Run testing script:
  ```bash
  python -X utf8 05-scaling-reliability/production/test_stateless.py
  ```
  Output:
  ```
  ============================================================
  Stateless Scaling Demo
  ============================================================
  Session ID: a09b5d91-c29d-44c3-8fa9-f1dddfef7795
  
  Request 1: [instance-a718c1]
    Q: What is Docker?
    A: Container là cách đóng gói app để chạy ở mọi nơi...
  
  Request 2: [instance-cedfe2]
    Q: Why do we need containers?
    A: Tôi là AI agent được deploy lên cloud...
  
  Request 3: [instance-a718c1]
    Q: What is Kubernetes?
    A: Tôi là AI agent được deploy lên cloud...
  
  Request 4: [instance-cedfe2]
    Q: How does load balancing work?
    A: Tôi là AI agent được deploy lên cloud...
  
  Request 5: [instance-a718c1]
    Q: What is Redis used for?
    A: Agent đang hoạt động tốt!...
  
  ------------------------------------------------------------
  Total requests: 5
  Instances used: {'instance-a718c1', 'instance-cedfe2'}
  ✅ All requests served despite different instances!
  
  --- Conversation History ---
  Total messages: 10
  ...
  ✅ Session history preserved across all instances via Redis!
  ```

- **Failover Verification**:
  1. Ran the test script which routed requests to `instance-a718c1` and `instance-cedfe2`.
  2. Stopped the active instance using `docker stop production-agent-2`.
  3. Re-ran the test script. All subsequent requests were successfully routed to the remaining active instance (`instance-a718c1` or `instance-cedfe2`), and the session chat history remained completely intact, proving the design is stateless and self-healing.

---

### Discussion Questions

#### 1. Tại sao dynamic load balancing quan trọng trong cloud-native architecture?
- **High Availability (Sẵn sàng cao)**: Khi một instance bị lỗi, load balancer phát hiện qua health checks và tự động ngừng route traffic đến instance đó, chuyển sang các instance lành mạnh khác mà người dùng không nhận thấy gián đoạn.
- **Auto-scaling (Tự động co giãn)**: Khi lượng traffic tăng/giảm, hệ thống tự động khởi chạy/tắt các container instance. Load balancer động sẽ đăng ký/hủy đăng ký các instance mới và phân phối tải đều đặn.
- **Zero-downtime Deployments (Triển khai không gián đoạn)**: Cho phép thực hiện rolling updates (cập nhật cuốn chiếu) bằng cách rút dần từng instance cũ ra, deploy bản mới, chạy health check thành công rồi mới đưa lại vào load balancer.

#### 2. Sự khác biệt giữa liveness probe và readiness probe là gì?
- **Liveness Probe**: Xác định xem ứng dụng có bị treo, rò rỉ bộ nhớ hoặc rơi vào vòng lặp vô hạn hay không. Nếu liveness probe thất bại liên tục (ví dụ: trả về 500 hoặc timeout), orchestrator sẽ lập tức kill container và restart nó.
- **Readiness Probe**: Xác định xem ứng dụng đã sẵn sàng xử lý request chưa (ví dụ: đã load xong các config cồng kềnh, thiết lập xong kết nối tới database và Redis chưa). Nếu readiness probe thất bại, load balancer chỉ tạm thời loại bỏ container khỏi danh sách route traffic, chứ không restart nó. Khi container sẵn sàng trở lại, nó sẽ nhận traffic bình thường.

#### 3. Thiết kế stateless agent có nhược điểm hay trade-off gì không?
- **Tăng Network Latency**: Mỗi request đều phải thực hiện thêm thao tác kết nối qua network (roundtrip) đến Redis để load và save session/history thay vì đọc trực tiếp trong bộ nhớ RAM cục bộ, làm tăng tổng thời gian phản hồi (latency).
- **Tăng Độ Phức Tạp Hệ Thống**: Cần cấu hình và vận hành thêm hệ thống Redis (hoặc database lưu session) có tính chịu lỗi cao (Redis Sentinel/Cluster), đồng thời phải viết thêm logic serialization/deserialization dữ liệu.
- **Chi phí**: Tải thêm hạ tầng caching có thể tăng chi phí vận hành dịch vụ trên cloud.
- **Vấn đề đồng thời (Concurrency / Race Conditions)**: Khi hai request đồng thời từ một user gửi đến hai instance khác nhau, có thể xảy ra race condition khi cập nhật lịch sử chat nếu không cấu hình cơ chế lock (distributed locking) hoặc transaction phù hợp.

